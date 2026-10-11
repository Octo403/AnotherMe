import json
from pathlib import Path

import faiss
import numpy as np
import pytest

from aitown.config import Settings
from aitown.question_bank.models import BankConfig,COURSE_ID,Page,ParsedSource,Question,SearchArgs,Source
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.search import QuestionBank
from aitown.ui.demo import settings_for_ui
from aitown.ui.service import UIError,UIService


@pytest.fixture
def multi_ui(tmp_path):
    settings=settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'app')
    config=BankConfig(root=settings.question_bank_dir)
    with BankRepository(config.database) as repo:
        repo.save_discovery([{'id':COURSE_ID,'name':'集合与图论','path':'public/resources/集合与图论'},
            {'id':'线性代数','name':'线性代数','path':'public/resources/线性代数'}],[])
        for cid,year,texts in [(COURSE_ID,2023,['一、证明给定图为欧拉图。']),
                ('线性代数',2024,['一、证明矩阵满足给定的秩条件。','二、证明特征值满足给定条件。'])]:
            with repo.conn:
                sid=repo.save_source(Source(course_id=cid,repository='synthetic/repo',path=cid+'/exam.txt',url='https://example.org/'+cid))
            repo.replace_parsed(sid,ParsedSource(pages=[Page(ordinal=1,text='\n'.join(texts),location_kind='line')],
                questions=[Question(number=str(i+1),text=t,pages=[1],question_type='证明') for i,t in enumerate(texts)],
                year=year,exam='final',category='exam'))
    service=UIService(settings,tmp_path/'ui.sqlite3')
    try:yield service,config
    finally:service.close()


def ask(service,session,text):
    return service.call('_chat',{'session':session,'question':text})['reply']


def test_dynamic_catalog_mentions_and_switch_do_not_reuse_other_course_question_or_filters(multi_ui):
    service,config=multi_ui
    courses=service.call('_bootstrap')['question_courses']
    assert {c['id'] for c in courses}=={COURSE_ID,'线性代数'}
    sid=service.call('_create_session',{})['id']
    first=ask(service,sid,'@集合与图论 找2023年期末证明题')
    assert first['questions'] and first['questions'][0]['course_id']==COURSE_ID
    service.call('_question_scope',{'session':sid,'course':'线性代数'})
    stale=ask(service,sid,'把第一题完整给我')
    assert stale['stop_reason']=='clarification' and not stale['questions']
    linear=ask(service,sid,'找2024年期末矩阵的证明题')
    assert linear['questions'] and all(q['course_id']=='线性代数' for q in linear['questions'])
    changed=ask(service,sid,'换成特征值')
    assert changed['questions'] and changed['debug']['decision']['calls'][0]['arguments']['year']==2024
    assert '矩阵' not in changed['debug']['decision']['calls'][0]['arguments']['query']
    old=ask(service,sid,'@集合与图论 找证明题')
    assert old['questions'] and old['questions'][0]['year']==2023
    assert 'year' not in old['debug']['decision']['calls'][0]['arguments']


def unprepare(config):
    with BankRepository(config.database) as repo:
        source=repo.sources('线性代数')[0]
        with repo.conn:
            repo.conn.execute('DELETE FROM questions WHERE source_id=?',(source['id'],))
            repo.conn.execute('DELETE FROM pages WHERE source_id=?',(source['id'],))
        repo.set_source(source['id'],status='discovered',processed_at=None)


def fake_launch(monkeypatch,config):
    from aitown.question_bank import scheduler
    from aitown.question_bank.jobs import create_job
    calls=[]
    def launch(selected,course,**kwargs):
        calls.append((selected,course,kwargs))
        with BankRepository(config.database) as repo:
            job=create_job(repo,course,max_files=kwargs.get('max_files'),update=kwargs.get('update',False))
        return {'job_id':job,'reused':len(calls)>1}
    monkeypatch.setattr(scheduler,'launch',launch)
    return calls


def test_prepare_input_launches_only_selected_course_and_keeps_selected_parser(multi_ui,monkeypatch):
    service,config=multi_ui;unprepare(config)
    calls=fake_launch(monkeypatch,config)
    sid=service.call('_create_session',{})['id']
    def configure():
        service.agent.question_bank.config.extractor='mineru_api'
        service.agent.question_bank.config.mineru_api_key='synthetic-secret'
    service.pool.submit(configure).result()
    result=service.call('_prepare_course',{'course':'线性代数','session':sid,'max_files':3})
    assert result['course']=='线性代数' and not result['warning']
    assert calls[0][0].extractor=='mineru_api' and calls[0][0].mineru_api_key=='synthetic-secret'
    assert service.call('_history',sid)['session']['question_course']=='线性代数'
    with BankRepository(config.database,readonly=True) as repo:
        job=repo.conn.execute('SELECT source_ids FROM jobs WHERE id=?',(result['job_id'],)).fetchone()
        assert all(repo.source(source_id)['course_id']=='线性代数' for source_id in json.loads(job[0]))
    with pytest.raises(UIError):service.call('_prepare_course',{'course':'不存在的课程','session':sid})
    with pytest.raises(UIError):service.call('_prepare_course',{'course':'线性代数','max_files':0})
    assert len(calls)==1


@pytest.mark.parametrize('prompt',['帮我准备线性代数知识库','帮我加入线性代数课程','我要准备线性代数'])
def test_natural_preparation_and_first_search_start_background_job_without_personal_writes(multi_ui,monkeypatch,prompt):
    service,config=multi_ui;unprepare(config)
    calls=fake_launch(monkeypatch,config)
    sid=service.call('_create_session',{'scope':'synthetic'})['id']
    count=lambda:service.agent.runtime_repository.conn.execute('SELECT count(*) FROM facts').fetchone()[0]
    before=service.pool.submit(count).result()
    response=ask(service,sid,prompt)
    assert len(calls)==1 and response['skills'][0]['name']=='course_status' and '准备任务' in response['answer']
    response=ask(service,sid,'矩阵的有哪些')
    assert len(calls)==1 and response['skills'][0]['data']['status']=='preparing'
    assert '正在准备' in response['answer']
    assert service.pool.submit(count).result()==before
    assert not response['profile'] and not response['style']


def test_first_at_search_prepares_new_course_but_personal_message_does_not(multi_ui,monkeypatch):
    service,config=multi_ui;unprepare(config)
    calls=fake_launch(monkeypatch,config)
    sid=service.call('_create_session',{})['id']
    ask(service,sid,'@线性代数')
    ask(service,sid,'记住：我喜欢咖啡')
    assert not calls
    response=ask(service,sid,'矩阵的有哪些')
    assert len(calls)==1 and response['skills'][0]['data']['status']=='preparing'


def test_other_course_broad_query_and_topics_do_not_use_graph_theory_tags(multi_ui,tmp_path):
    service,config=multi_ui
    bank=QuestionBank(config)
    result=bank.search(SearchArgs(course='线性代数',query='找线性代数的证明题',question_type='证明'))
    assert result.method=='filters' and result.count==2
    from aitown.question_bank.prepare import parse_file
    path=tmp_path/'linear.txt';path.write_text('1. 证明函数和矩阵满足给定条件。',encoding='utf-8')
    parsed=parse_file(path,{'course_id':'线性代数','category_hint':'exam'},tmp_path/'out',config)
    assert parsed.questions and parsed.questions[0].topics==[] and parsed.questions[0].question_type=='证明'


def test_combination_area_matches_long_stem_with_literal_permutation_term(multi_ui):
    _,config=multi_ui
    with BankRepository(config.database) as repo:
        source=repo.sources(COURSE_ID)[0]
        repo.replace_parsed(source['id'],ParsedSource(pages=[Page(ordinal=1,text='public question')],
            questions=[Question(number='二',text='二、计算满足条件的排列数。'+'数学条件较长。'*500,pages=[1])]))
    result=QuestionBank(config).search(SearchArgs(course=COURSE_ID,query='排列组合的有哪些'))
    assert result.results and result.results[0]['number']=='二'


def test_private_pdf_uses_local_parser_without_upload(tmp_path,monkeypatch):
    import pymupdf as fitz
    from aitown.question_bank import mineru_api
    from aitown.question_bank.prepare import parse_file
    def forbidden(*args):raise AssertionError('未授权的压缩包成员不得上传')
    monkeypatch.setattr(mineru_api,'parse_mineru_api_pdf',forbidden)
    path=tmp_path/'member.pdf'
    with fitz.open() as pdf:
        page=pdf.new_page();page.insert_text((30,40),'1. Prove the condition')
        pdf.save(path)
    source={'course_id':COURSE_ID,'repository':'private/course','path':'public/resources/集合与图论/exam.zip',
        'member':'exam.pdf','url':'https://raw.githubusercontent.com/victkk/FDU-Sharing/master/public/resources/集合与图论/exam.zip','category_hint':'exam'}
    parsed=parse_file(path,source,tmp_path/'out',BankConfig(root=tmp_path/'bank',extractor='mineru_api'))
    assert len(parsed.questions)==1 and parsed.questions[0].images
    assert any('未上传文件' in note for note in parsed.notes)


def test_discovered_course_refresh_does_not_download_any_material(multi_ui,monkeypatch):
    service,config=multi_ui
    from aitown.question_bank import sources
    class Remote:
        def __init__(self,*args):pass
        def discover(self):
            return [{'id':'数据结构','name':'数据结构','path':'public/resources/数据结构'}],[
                Source(course_id='数据结构',repository='synthetic/repo',path='public/resources/数据结构/exam.pdf',url='https://example.org/exam.pdf')]
        def download(self,*args,**kwargs):raise AssertionError('目录刷新不能下载资料')
        def close(self):pass
    monkeypatch.setattr(sources,'GithubSource',Remote)
    result=service.call('_discover_courses',{})
    assert any(c['id']=='数据结构' and c['questions']==0 for c in result['courses'])
    assert not (config.root/'raw').exists()


def test_course_indexes_are_independent_and_contain_only_their_course_ids(multi_ui,tmp_path):
    _,config=multi_ui
    from aitown.question_bank.semantic import build_index,index_directory
    class Embedding:
        name='synthetic-embedding'
        def documents(self,texts):return np.asarray([[1.,0.] for _ in texts],dtype=np.float32)
    config.embedding_dir=tmp_path/'model'
    build_index(config,Embedding(),course=COURSE_ID)
    first=index_directory(config,COURSE_ID)/'questions.faiss'
    content=first.read_bytes()
    build_index(config,Embedding(),course='线性代数')
    other=index_directory(config,'线性代数')/'questions.faiss'
    assert other!=first and first.read_bytes()==content
    with BankRepository(config.database,readonly=True) as repo:
        for course,path in [(COURSE_ID,first),('线性代数',other)]:
            index=faiss.read_index(str(path))
            assert set(map(int,faiss.vector_to_array(index.id_map)))=={q['id'] for q in repo.rows(course)}


def test_scheduler_passes_actual_course_to_worker_and_reuses_same_course_job(multi_ui,monkeypatch):
    _,config=multi_ui;unprepare(config)
    from aitown.question_bank import scheduler
    calls=[]
    class Process:
        pid=12345
        def __init__(self,command,**kwargs):calls.append(command)
    monkeypatch.setattr(scheduler.subprocess,'Popen',Process)
    first=scheduler.launch(config,'线性代数')
    second=scheduler.launch(config,'线性代数')
    assert second['reused'] and first['job_id']==second['job_id'] and len(calls)==1
    assert calls[0][-2:]==['--course','线性代数']


def test_local_course_files_list_download_and_reject_cross_course_or_external_paths(multi_ui):
    service,config=multi_ui
    with BankRepository(config.database) as repo:
        source=repo.sources('线性代数')[0]
        raw=config.root/'raw'/'exam.txt';raw.parent.mkdir(parents=True);raw.write_text('本地原件',encoding='utf-8')
        repo.set_source(source['id'],local_path=str(raw))
    status=service.call('_course_status','线性代数')
    file=status['files'][0]
    assert file['id']==source['id'] and file['local_available'] and file['questions']==2
    assert file['file_kind']=='original' and 'local_path' not in file
    body,_,name=service.call('_course_source','线性代数',source['id'])
    assert body.decode('utf-8')=='本地原件' and name=='exam.txt'
    with pytest.raises(UIError):service.call('_course_source',COURSE_ID,source['id'])
    with BankRepository(config.database) as repo:
        repo.set_source(source['id'],local_path=str(service.metadata_path))
    with pytest.raises(UIError):service.call('_course_source','线性代数',source['id'])
    assert not service.call('_course_status','线性代数')['files'][0]['local_available']


def test_course_session_model_preferences_and_local_files_survive_new_service(multi_ui):
    from contextlib import closing
    service,config=multi_ui
    sid=service.call('_create_session',{})['id']
    first=ask(service,sid,'@线性代数 找证明题')
    service.call('_save_model_settings',{'search_mode':'codex_chatgpt','codex_model':'user-selected-model','extractor':'basic'})
    with closing(UIService(service.settings,service.metadata_path)) as restored:
        history=restored.call('_history',sid)
        assert history['session']['question_course']=='线性代数'
        assert [q['id'] for q in history['turns'][0]['reply']['questions']]==[q['id'] for q in first['questions']]
        assert restored.call('_model_settings')['codex_model']=='user-selected-model'
        assert restored.call('_model_settings')['search_mode']=='codex_chatgpt'
        assert restored.call('_course_status','线性代数')['files'][0]['questions']==2


def test_dead_queued_course_job_is_visible_as_interrupted_and_can_be_restarted(multi_ui,monkeypatch):
    service,config=multi_ui;unprepare(config)
    from aitown.question_bank import scheduler
    from aitown.question_bank.jobs import create_job
    with BankRepository(config.database) as repo:
        old=create_job(repo,'线性代数')
        with repo.conn:repo.conn.execute('UPDATE jobs SET owner_pid=NULL,updated_at=0 WHERE id=?',(old,))
    assert service.call('_course_status','线性代数')['jobs'][0]['status']=='interrupted'
    class Process:
        pid=12345
        def __init__(self,*args,**kwargs):pass
    monkeypatch.setattr(scheduler.subprocess,'Popen',Process)
    result=scheduler.launch(config,'线性代数')
    assert not result['reused'] and result['job_id']!=old
    with BankRepository(config.database) as repo:
        assert repo.conn.execute('SELECT status FROM jobs WHERE id=?',(old,)).fetchone()[0]=='interrupted'
