import io
import json
import socket
import threading
import zipfile
from pathlib import Path

import httpx
import pytest

from aitown.config import Settings
from aitown.question_bank import jobs, scheduler, supplements
from aitown.question_bank.cleanup import cleanup_raw
from aitown.question_bank.files import source_summary
from aitown.question_bank.models import BankConfig, SearchArgs
from aitown.question_bank.prepare import parse_file
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.search import QuestionBank
from aitown.ui.demo import settings_for_ui
from aitown.ui.server import make_server
from aitown.ui.service import UIService


PAPER = '# 1. 等价关系\n证明等价关系的等价类构成划分。\n\n# 2. 欧拉图\n证明欧拉图各顶点度为偶数。\n'.encode()
HTTP_CLIENT = httpx.Client


@pytest.fixture
def bank(tmp_path):
    config = BankConfig(root=tmp_path/'bank')
    with BankRepository(config.database) as repo:
        yield config, repo


def uploaded(config, repo, name='exam.md', content=PAPER, course='自建课程', replace=False):
    return supplements.add_uploads(repo, config, course, [{'name':name,'content':content}], replace=replace)


def test_upload_new_course_reuse_replace_and_isolation(bank):
    config, repo = bank
    course, items = uploaded(config, repo)
    sid = items[0]['id'];original = Path(repo.source(sid)['local_path'])
    job = jobs.create_job(repo, course['id'], source_ids=[sid])
    jobs.run_job(config, job, progress=lambda _:None)
    assert original.read_bytes() == PAPER
    result = QuestionBank(config).search(SearchArgs(course=course['id'],query='欧拉图'))
    assert result.count == 1 and result.results[0]['content_format'] == 'markdown'
    assert result.results[0]['url'] == '' and not result.results[0]['images']
    old_id = result.results[0]['id']
    _, duplicate = uploaded(config, repo, name='EXAM.MD', content=b'ignored')
    assert duplicate[0]['id'] == sid and duplicate[0]['reused'] and original.read_bytes() == PAPER
    _, other = uploaded(config, repo, course='另一门课')
    assert other[0]['id'] != sid
    _, replacement = uploaded(config, repo, content='# 1. 欧拉图\n证明欧拉回路的存在条件。'.encode(), replace=True)
    assert replacement[0]['id'] == sid and not replacement[0]['reused']
    assert original.read_bytes()==PAPER and Path(repo.source(sid)['local_path'])!=original
    assert not QuestionBank(config).search(SearchArgs(course=course['id'],query='欧拉图')).count
    jobs.run_job(config, jobs.create_job(repo, course['id'], source_ids=[sid]), progress=lambda _:None)
    current = QuestionBank(config).search(SearchArgs(course=course['id'],query='欧拉图'))
    assert current.question_ids and current.question_ids[0] != old_id
    assert not cleanup_raw(config, course['id'])['deleted']
    assert source_summary(config.root, repo.source(sid))['origin_kind'] == 'upload'


@pytest.mark.parametrize('name', ['../exam.md',r'a\exam.pdf','CON.pdf','test.exe','exam.md.'])
def test_reject_upload_paths_unsupported_names_without_creating_course(bank,name):
    config, repo = bank
    with pytest.raises(ValueError):uploaded(config, repo, name=name)
    assert not repo.courses()


def test_upload_limits_and_concurrent_replacement(bank):
    config, repo = bank
    with pytest.raises(ValueError):uploaded(config, repo, content=b'')
    with pytest.raises(ValueError):uploaded(config.model_copy(update={'max_download_bytes':4}), repo)
    course, items = uploaded(config, repo)
    jobs.create_job(repo, course['id'], source_ids=[items[0]['id']])
    with pytest.raises(ValueError,match='正在运行'):uploaded(config, repo, replace=True)
    _, extra = uploaded(config, repo, name='extra.md')
    assert extra[0]['id'] != items[0]['id']


def test_upload_copy_failure_remains_visible_and_other_file_commits(bank,monkeypatch):
    config, repo = bank
    original = Path.replace
    def fail_one(path,target):
        if target.name == 'bad.md':raise OSError('simulated disk error')
        return original(path,target)
    monkeypatch.setattr(Path,'replace',fail_one)
    course, items = supplements.add_uploads(repo,config,'自建课程',[
        {'name':'good.md','content':PAPER},{'name':'bad.md','content':PAPER}])
    assert not items[0].get('error') and items[1]['error']
    assert Path(repo.source(items[0]['id'])['local_path']).read_bytes() == PAPER
    assert repo.source(items[1]['id'])['status'] == 'failed'


def mock_network(monkeypatch,handler,addresses=None):
    monkeypatch.setattr(supplements.socket,'getaddrinfo',lambda host,*a,**kw:[
        (socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443)) for ip in (addresses or {}).get(host,['93.184.216.34'])])
    monkeypatch.setattr(supplements.httpx,'Client',lambda **kw:HTTP_CLIENT(transport=httpx.MockTransport(handler),**kw))


def test_public_url_download_pins_host_preserves_md_and_refreshes(bank,monkeypatch):
    config, repo = bank;calls=[]
    def handle(request):
        calls.append(request)
        assert request.url.host == '93.184.216.34' and request.headers['Host'] == 'example.org'
        assert request.extensions['sni_hostname'] == 'example.org'
        assert request.extensions['timeout']['connect']<=5
        return httpx.Response(200,headers={'content-type':'text/plain'},content=PAPER if len(calls)==1 else b'# 1. Updated\nFind a matrix.')
    mock_network(monkeypatch,handle)
    course, items = supplements.add_urls(repo,'网址课程',['https://example.org/exam.md#section','https://example.org/exam.md'])
    assert len(items)==1
    sid=items[0]['id'];jobs.run_job(config,jobs.create_job(repo,course['id'],source_ids=[sid]),progress=lambda _:None)
    source=repo.source(sid);assert Path(source['local_path']).suffix=='.md'
    assert Path(source['local_path']).read_bytes()==PAPER
    _, duplicate=supplements.add_urls(repo,'网址课程',['https://example.org/exam.md'])
    assert duplicate[0]['reused'] and len(repo.sources(course['id']))==1
    supplements.add_urls(repo,'网址课程',['https://example.org/exam.md'],replace=True)
    jobs.run_job(config,jobs.create_job(repo,course['id'],source_ids=[sid]),progress=lambda _:None)
    assert len(calls)==2 and b'Updated' in Path(repo.source(sid)['local_path']).read_bytes()


@pytest.mark.parametrize('addresses', [['127.0.0.1'],['10.0.0.1'],['169.254.169.254'],['93.184.216.34','192.168.1.2'],['::1']])
def test_public_url_rejects_private_and_mixed_dns(bank,monkeypatch,addresses):
    config, repo=bank;requests=[]
    mock_network(monkeypatch,lambda req:requests.append(req),{'example.org':addresses})
    _, items=supplements.add_urls(repo,'网址课程',['https://example.org/file.pdf'])
    with pytest.raises(ValueError,match='公网'):supplements.download_url(repo,repo.source(items[0]['id']),config)
    assert not requests


def test_redirect_private_block_and_download_bound(bank,monkeypatch):
    config, repo=bank;requests=[]
    def handle(req):
        requests.append(req)
        return httpx.Response(302,headers={'location':'http://internal.invalid/secret'})
    mock_network(monkeypatch,handle,{'internal.invalid':['127.0.0.1']})
    _, items=supplements.add_urls(repo,'网址课程',['https://example.org/page'])
    source=repo.source(items[0]['id'])
    with pytest.raises(ValueError,match='公网'):supplements.download_url(repo,source,config)
    assert len(requests)==1
    mock_network(monkeypatch,lambda req:httpx.Response(200,headers={'content-type':'application/pdf'},content=b'%PDF-'+b'x'*20))
    with pytest.raises(ValueError,match='大小|过大'):supplements.download_url(repo,source,config.model_copy(update={'max_download_bytes':10}))
    assert not list((config.root/'raw'/str(source['id'])).iterdir())


def test_html_questions_attachment_links_and_no_invented_question(bank,monkeypatch):
    config, repo=bank
    page=b'<title>Course</title><nav>private nav</nav><h2>1. Matrix</h2><p>Find matrix rank.</p><a href="exam.pdf">Exam PDF</a><script>evil()</script>'
    def handle(req):
        if req.url.path=='/page':return httpx.Response(302,headers={'location':'https://example.org/course/index.html'})
        return httpx.Response(200,headers={'content-type':'text/html'},content=page)
    mock_network(monkeypatch,handle)
    course,items=supplements.add_urls(repo,'网址课程',['https://example.org/page'])
    sid=items[0]['id'];jobs.run_job(config,jobs.create_job(repo,course['id'],source_ids=[sid]),progress=lambda _:None)
    summary=source_summary(config.root,repo.source(sid))
    assert summary['links']==[{'url':'https://example.org/course/exam.pdf','label':'Exam PDF'}]
    assert len(repo.rows(course['id']))==1 and 'evil' not in repo.rows(course['id'])[0]['text']
    path=config.root/'article.html';path.write_text('<h1>Lecture notes</h1><p>No numbered questions.</p>',encoding='utf-8')
    parsed=parse_file(path,repo.source(sid),config.root/'processed'/'article',config)
    assert not parsed.questions and parsed.status=='partial' and parsed.notes


def test_upload_archive_and_custom_pdf_never_use_online_parser(bank,monkeypatch):
    fitz=pytest.importorskip('fitz');config,repo=bank
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:z.writestr('exam.md',PAPER)
    course,items=uploaded(config,repo,name='papers.zip',content=archive.getvalue())
    pdf=fitz.open();page=pdf.new_page();page.insert_text((50,70),'1. Calculate matrix rank.');pdf_bytes=pdf.tobytes();pdf.close()
    _, files=uploaded(config,repo,name='exam.pdf',content=pdf_bytes)
    import aitown.question_bank.mineru_api as mineru
    monkeypatch.setattr(mineru,'parse_mineru_api_pdf',lambda *a,**kw:pytest.fail('custom source must stay local'))
    result=jobs.run_job(config.model_copy(update={'extractor':'mineru_api'}),jobs.create_job(repo,course['id'],source_ids=[items[0]['id'],files[0]['id']]),progress=lambda _:None)
    assert result['failed']==0 and result['done']==3 and len(repo.rows(course['id']))>=2
    assert not cleanup_raw(config,course['id'])['deleted']


def test_additions_during_worker_are_queued_without_retrying_failures(bank,monkeypatch):
    config,repo=bank;course,items=uploaded(config,repo);launched=[];added=[]
    def add_during_parse(_):
        if not added:added.extend(uploaded(config,repo,name='later.md')[1])
    monkeypatch.setattr(scheduler,'launch',lambda *a,**kw:launched.append(kw))
    result=jobs.run_job(config,jobs.create_job(repo,course['id'],source_ids=[items[0]['id']]),progress=add_during_parse)
    assert result['done']==1 and launched==[{'source_ids':[added[0]['id']]}]
    assert repo.source(added[0]['id'])['status']=='downloaded'
    def fail_next(*a,**kw):raise OSError('spawn unavailable')
    monkeypatch.setattr(scheduler,'launch',fail_next)
    job=jobs.create_job(repo,course['id'],source_ids=[items[0]['id']])
    jobs.run_job(config,job,progress=lambda _:None)
    row=repo.conn.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone()
    assert row['status']=='complete' and '追加资料' in row['error']


def test_multipart_and_url_http_persistence_scope_and_origin(tmp_path,monkeypatch):
    cfg=settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'app')
    service=UIService(cfg,tmp_path/'ui.sqlite3');server=make_server(service,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    launches=[]
    monkeypatch.setattr(scheduler,'launch',lambda config,course,**kw:launches.append((course,kw)) or {'job_id':9,'reused':False})
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False) as client:
            session=client.post('/api/session',json={}).json()['id']
            assert client.get('/api/bootstrap').json()['question_bank_directory']==str(cfg.question_bank_dir.resolve())
            response=client.post('/api/course-upload',data={'course':'用户课程','session':session,'replace':'false'},files=[('files',('exam.md',PAPER,'text/markdown'))])
            assert response.status_code==200,response.text
            result=response.json();sid=result['items'][0]['id']
            assert launches==[('用户课程',{'source_ids':[sid]})]
            original=client.get('/api/course-source',params={'course':'用户课程','source':sid})
            assert original.status_code==200 and original.content==PAPER and 'attachment' in original.headers['content-disposition']
            assert client.get('/api/history',params={'session':session}).json()['session']['question_course']=='用户课程'
            assert client.get('/api/course-source',params={'course':'另一门课','source':sid}).status_code==404
            duplicate=client.post('/api/course-upload',data={'course':'用户课程'},files={'files':('exam.md',PAPER)})
            assert duplicate.json()['items'][0]['reused'] and len(launches)==1
            assert client.post('/api/course-upload',json={}).status_code==415
            assert client.post('/api/course-upload',headers={'Origin':'https://bad.invalid'},data={'course':'不应创建'},files={'files':('other.md',PAPER)}).status_code==403
            assert client.post('/api/course-upload',data={'course':'用户课程'},files={'files':('../outside.md',PAPER)}).status_code==400
            assert client.post('/api/course-urls',json={'course':'用户课程','urls':['file:///C:/secret.pdf']}).status_code==400
            response=client.post('/api/course-urls',json={'course':'网址新课程','urls':['https://example.org/exam.pdf']})
            assert response.status_code==200 and response.json()['course']['name']=='网址新课程'
            assert client.post('/api/course-retry',json={'course':'用户课程','source':sid}).status_code==400
            with BankRepository(cfg.question_bank_dir/'question_bank.sqlite3') as repo:
                repo.set_source(sid,status='failed',error='parse interrupted')
            assert client.post('/api/course-retry',json={'course':'网址新课程','source':sid}).status_code==404
            retry=client.post('/api/course-retry',json={'course':'用户课程','source':sid})
            assert retry.status_code==200 and launches[-1]==('用户课程',{'source_ids':[sid]})
            with BankRepository(cfg.question_bank_dir/'question_bank.sqlite3') as repo:
                jobs.create_job(repo,'用户课程',source_ids=[sid])
            assert client.post('/api/course-retry',json={'course':'用户课程','source':sid}).status_code==409
        def reopen():service.agent.close();service.meta.close();service._start()
        service.pool.submit(reopen).result()
        assert any(c['name']=='用户课程' for c in service.call('_bootstrap')['question_courses'])
    finally:server.shutdown();server.server_close();thread.join();service.close()


def test_html_keeps_image_gaps_and_table_boundaries(bank):
    config,repo=bank;course,items=uploaded(config,repo,name='exam.html',content=b'<h2>1. Matrix</h2><p>Use diagram <img alt="matrix A" src="a.png"></p><table><tr><td>1</td><td>2</td></tr></table>')
    sid=items[0]['id'];path=Path(repo.source(sid)['local_path'])
    parsed=parse_file(path,repo.source(sid),config.root/'processed'/str(sid),config)
    assert len(parsed.questions)==1 and parsed.status=='partial'
    assert 'matrix A' in parsed.questions[0].text and '1 | 2' in parsed.questions[0].text
    assert parsed.questions[0].missing and parsed.notes


def test_download_filename_marks_answers_and_keeps_url_path_short(bank,monkeypatch):
    config,repo=bank
    mock_network(monkeypatch,lambda req:httpx.Response(200,headers={'content-type':'text/markdown',
        'content-disposition':"attachment; filename*=utf-8''%E7%AD%94%E6%A1%88.md"},content=PAPER))
    course,items=supplements.add_urls(repo,'答案资料',['https://example.org/download?id=123'])
    sid=items[0]['id'];jobs.run_job(config,jobs.create_job(repo,course['id'],source_ids=[sid]),progress=lambda _:None)
    source=repo.source(sid)
    assert source['category']=='answer' and not repo.rows(course['id'])
    assert source['url']=='https://example.org/download?id=123' and source['path']==f'supplement/urls/{sid}/答案.md'


def test_replacing_archive_hides_removed_nested_questions(bank):
    config,repo=bank
    inner=io.BytesIO()
    with zipfile.ZipFile(inner,'w') as z:z.writestr('old.md',PAPER)
    outer=io.BytesIO()
    with zipfile.ZipFile(outer,'w') as z:z.writestr('inner.zip',inner.getvalue())
    course,items=uploaded(config,repo,name='papers.zip',content=outer.getvalue())
    sid=items[0]['id'];jobs.run_job(config,jobs.create_job(repo,course['id'],source_ids=[sid]),progress=lambda _:None)
    assert len(repo.rows(course['id']))==2
    replacement=io.BytesIO()
    with zipfile.ZipFile(replacement,'w') as z:z.writestr('new.md',b'# 1. New\nFind matrix rank.')
    _,result=uploaded(config,repo,name='papers.zip',content=replacement.getvalue(),replace=True)
    assert not result[0].get('error'),repo.source(sid)['error']
    assert not repo.rows(course['id'])
    jobs.run_job(config,jobs.create_job(repo,course['id'],source_ids=[sid]),progress=lambda _:None)
    assert len(repo.rows(course['id']))==1 and repo.rows(course['id'])[0]['text'].endswith('Find matrix rank.')


