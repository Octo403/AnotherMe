"""Live public linear-algebra acquisition through the UI, in an isolated bank."""
import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pymupdf

from aitown.config import Settings
from aitown.question_bank.models import BankConfig,COURSE_ID,Page,ParsedSource,Question,Source
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.semantic import index_directory
from aitown.ui.demo import settings_for_ui
from aitown.ui.server import make_server
from aitown.ui.service import UIService


def public_snapshot():
    with sqlite3.connect(Path('data/question_bank/question_bank.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
        return {table:db.execute('SELECT * FROM '+table+' ORDER BY 1,2').fetchall()
                for table in ('courses','sources','questions','pages','rendered_pages','answers','question_duplicates','sqlite_sequence')}


def main():
    before=public_snapshot()
    root=Path('data/runtime/course-self-service-check');root.mkdir(parents=True,exist_ok=True)
    run=Path(tempfile.mkdtemp(prefix='linear-',dir=root)).resolve()
    config=BankConfig(root=run/'bank')
    with BankRepository(Path('data/question_bank/question_bank.sqlite3'),readonly=True) as original:
        selected=original.sources('线性代数')
        assert selected
    with BankRepository(config.database) as repo:
        repo.save_discovery([{'id':'线性代数','name':'线性代数','path':'public/resources/线性代数'},
            {'id':COURSE_ID,'name':'集合与图论','path':'synthetic/course'}],[Source.model_validate(source) for source in selected])
        with repo.conn:
            sentinel=repo.save_source(Source(course_id=COURSE_ID,repository='synthetic/isolation',path='sentinel.txt',url='https://example.org/sentinel'))
        repo.replace_parsed(sentinel,ParsedSource(pages=[Page(ordinal=1,text='原有课程隔离样例',location_kind='line')],
            questions=[Question(number='一',text='一、证明隔离样例的图论条件。',pages=[1],question_type='证明')]))
        preserved=[tuple(row) for row in repo.conn.execute('SELECT * FROM questions WHERE source_id=?',(sentinel,))]
    installed=Settings.from_env(mode='demo')
    installed.question_bank_dir=config.root
    installed.question_similar_provider='none'
    installed.question_extractor='basic'
    installed.question_pdf_ocr='none'
    settings=settings_for_ui(installed,run/'app')
    assert settings.local_embedding_dir and settings.local_embedding_dir.is_dir()
    service=UIService(settings,run/'ui.sqlite3')
    server=make_server(service,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    checks={};samples=[]
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False,timeout=30) as client:
            def get(path,**kwargs):
                response=client.get(path,**kwargs);response.raise_for_status();return response.json()
            def post(path,payload):
                response=client.post(path,json=payload);response.raise_for_status();return response.json()
            session=post('/api/session',{'scope':''})['id']
            catalog=get('/api/bootstrap')['question_courses']
            checks['real_course_available_before_download']=any(c['id']=='线性代数' and c['files']==len(selected) and not c['questions'] for c in catalog)
            checks['course_form_and_script_served']='course-name' in client.get('/').text and client.get('/courses.js').status_code==200
            start=time.monotonic()
            job=post('/api/prepare-course',{'course':'线性代数','session':session,'max_files':2})
            checks['prepare_returns_background_job']=time.monotonic()-start<5 and bool(job.get('worker_pid'))
            stages=[];status=None
            while time.monotonic()-start<360:
                status=get('/api/course-status',params={'course':'线性代数'})
                latest=status['jobs'][0]
                key=(latest['status'],latest['stage'],len(latest['done_ids']))
                if not stages or stages[-1]!=key:
                    stages.append(key);print(json.dumps({'stage':key},ensure_ascii=False),flush=True)
                if latest['status'] not in ('queued','running'):break
                time.sleep(.3)
            assert status and status['jobs'][0]['status'] in ('complete','partial'),status
            checks['live_two_file_preparation_and_index']=status['coverage']['questions']>0 and (index_directory(config,'线性代数')/'questions.faiss').is_file()
            checks['selected_course_pinned']=get('/api/history',params={'session':session})['session']['question_course']=='线性代数'
            with BankRepository(config.database,readonly=True) as repo:
                current=[tuple(row) for row in repo.conn.execute('SELECT * FROM questions WHERE source_id=?',(sentinel,))]
                checks['other_course_records_untouched']=current==preserved and repo.source(sentinel)['status']=='ready'
                checks['only_requested_sources_processed']=len([s for s in repo.sources('线性代数') if s['processed_at']])==2
                checks['processed_originals_cleaned']=all(not s['local_path'] and s['raw_deleted_at'] for s in repo.sources('线性代数') if s['processed_at'])
                source=repo.sources('线性代数')[0]
                questions={q['number']:q for q in repo.rows('线性代数') if q['source_id']==source['id']}
                checks['heading_formula_fragments_stay_with_new_question']=questions['七']['pages']==[7] and questions['八']['pages']==[8]
                checks['spine_text_not_used_for_question_boundaries']=all('装' not in q['text'] for q in questions.values())
                with pymupdf.open(config.root/'processed'/str(source['id'])/'document.pdf') as pdf:
                    matrix_bottom=max(d['rect'].y1 for d in pdf[0].get_drawings()
                        if d['rect'].x0>pdf[0].rect.width*.15 and 60<d['rect'].height<pdf[0].rect.height*.5)
                    crop=next(r['bbox'] for r in questions['一']['image_regions'] if r['page']==1)
                    checks['matrix_bottom_and_delimiters_inside_crop']=crop[3]>=matrix_bottom
            def ask(text):
                pending=post('/api/chat',{'session':session,'question':text})['job_id']
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    response=get('/api/job',params={'id':pending})
                    if response['status']!='running':break
                    time.sleep(.05)
                assert response['status']=='complete',response
                reply=response['result']['reply']
                samples.append({'question':text,'ids':[q['id'] for q in reply['questions']],
                    'calls':[call['name'] for call in reply['debug']['decision']['calls']]})
                return reply
            first=ask('@线性代数 找两道题')
            checks['new_course_question_search']=bool(first['questions']) and all(q['course_id']=='线性代数' for q in first['questions'])
            picked=ask('把第一题完整给我')
            checks['new_course_ordinal_followup']=bool(picked['questions']) and picked['questions'][0]['id']==first['questions'][0]['id']
            changed=ask('换成矩阵相关的题')
            checks['new_course_topic_followup']=bool(changed['questions']) and all(q['course_id']=='线性代数' for q in changed['questions'])
            checks['local_bge_used']=changed['skills'][0]['data']['method']=='bge+keyword+bge_rerank'
            image_count=0
            for question in first['questions']:
                for index in range(len(question['images'])):
                    response=client.get('/api/question-image',params={'id':question['id'],'index':index});response.raise_for_status()
                    with pymupdf.open(stream=response.content,filetype='png') as png:assert png.page_count==1
                    image_count+=1
                for origin in question['origins']:
                    response=client.get('/api/question-source',params={'id':question['id'],'source':origin['source_id']});response.raise_for_status()
                    assert origin['file_kind']=='converted_pdf'
                    with pymupdf.open(stream=response.content,filetype='pdf') as pdf:assert pdf.page_count>0
            checks['new_word_images_and_converted_pdfs_readable']=image_count>0
            checks['ocr_hidden_answers_opt_in']=all('text' not in q and 'answers' not in q for r in (first,picked,changed) for q in r['questions'])
            checks['personal_context_excluded']=all(not r['profile'] and not r['style'] for r in (first,picked,changed))
    finally:
        server.shutdown();server.server_close();thread.join();service.close()
    checks['existing_public_bank_unchanged']=before==public_snapshot()
    report={'checks':checks,'passed':all(checks.values()),'run':str(run),'course':'线性代数',
        'requested_sources':[{k:s[k] for k in ('path','url','size')} for s in selected[:2]],
        'coverage':status['coverage'],'stages':stages,'samples':samples,'seconds':time.monotonic()-start,
        'note':'真实公开 Word 下载、本机 Word 转换、独立 worker/BGE 索引和 HTTP/Agent；独立题库和合成个人资料，未上传在线解析服务，不代表48份全量覆盖或数学条件逐题审校。'}
    Path('data/evaluation/question_bank/course-self-service.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    assert report['passed']


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
