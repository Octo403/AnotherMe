"""Real UI/worker/ChatGPT flow in a new local bank; never writes the user's bank."""
import argparse
import json
import threading
import time
import uuid
from pathlib import Path

import httpx

from aitown.config import Settings
from aitown.question_bank.repository import BankRepository
from aitown.ui.demo import settings_for_ui
from aitown.ui.server import make_server
from aitown.ui.service import UIService


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--course',default='线性代数')
    parser.add_argument('--max-files',type=int,default=2)
    parser.add_argument('--query',default='找两道矩阵相关的题')
    args=parser.parse_args()
    run_id=uuid.uuid4().hex
    run=(Path('data/runtime/local-course-flow')/run_id).resolve()
    report_path=Path('data/evaluation/question_bank')/f'local-course-flow-{run_id}.json'
    run.mkdir(parents=True)
    installed=Settings.from_env(mode='demo')
    selected=installed.model_copy(update={'question_bank_dir':run/'bank','question_similar_provider':'codex_chatgpt',
        'question_extractor':'basic','question_pdf_ocr':'none','mineru_api_key':''})
    settings=settings_for_ui(selected,run/'app')
    checks={};stages=[];samples=[];service=server=thread=client=None
    started=time.monotonic()
    report={'run':str(run),'course':args.course,'checks':checks,'stages':stages,'samples':samples,
        'note':'全新独立题库；实际公开目录、下载、本机解析、BGE 和 ChatGPT 登录筛选。仅小批资料，不代表整门课覆盖或数学内容全部审校；个人数据未参与。'}

    def stop():
        nonlocal service,server,thread,client
        if client:client.close();client=None
        if server:server.shutdown();server.server_close();thread.join();server=None
        if service:service.close();service=None

    def start():
        nonlocal service,server,thread,client
        service=UIService(settings,run/'ui.sqlite3')
        server=make_server(service,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        client=httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False,timeout=30)

    def get(path,**kwargs):
        response=client.get(path,**kwargs);response.raise_for_status();return response.json()

    def post(path,payload):
        response=client.post(path,json=payload);response.raise_for_status();return response.json()

    def operation(path,payload,timeout=300):
        job=post(path,payload)['job_id'];deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            result=get('/api/job',params={'id':job})
            if result['status']=='complete':return result['result']
            if result['status']=='failed':raise RuntimeError(result.get('error','操作失败'))
            time.sleep(.3)
        raise TimeoutError(path+' 超时')

    def chat(question):
        result=operation('/api/chat',{'session':session,'question':question})['reply']
        search=next((s['data'] for s in result['skills'] if s['name']=='question_search'),{})
        sample={'question':question,'ids':[q['id'] for q in result['questions']],
            'method':search.get('method'),'calls':search.get('model_calls',[]),'warnings':result['warnings'],
            'trace':result['debug']['trace'],'decision':result['debug']['decision']}
        samples.append(sample)
        print(json.dumps({'chat':question,'ids':sample['ids'],'method':sample['method'],
            'model_status':[c['status'] for c in sample['calls']]},ensure_ascii=False),flush=True)
        return result,search

    try:
        start()
        checks['direct_course_entry_served']='open-course-library' in client.get('/').text
        session=post('/api/session',{})['id']
        directory=operation('/api/course-catalog',{})
        course=next(c for c in directory['courses'] if c['name']==args.course)
        checks['course_input_resolves_live_public_catalog']=bool(course['files'])
        submitted=time.monotonic()
        job=post('/api/prepare-course',{'course':args.course,'session':session,'max_files':args.max_files})
        checks['prepare_returns_independent_worker']=bool(job.get('worker_pid')) and time.monotonic()-submitted<5
        deadline=time.monotonic()+480
        while time.monotonic()<deadline:
            status=get('/api/course-status',params={'course':course['id']})
            latest=status['jobs'][0]
            stage=(latest['status'],latest['stage'],len(latest['done_ids']))
            if not stages or stages[-1]!=stage:
                stages.append(stage);print(json.dumps({'preparation':stage},ensure_ascii=False),flush=True)
            if latest['status'] not in ('queued','running'):break
            time.sleep(.5)
        assert latest['status'] in ('complete','partial'),latest
        assert status['coverage']['questions']>0,status
        checks['processed_questions_and_progress_persisted']=bool(latest['done_ids'])
        report['coverage']=status['coverage']
        files=[f for f in status['files'] if f['local_available']]
        checks['originals_retained']=status['coverage']['raw_retained']>=args.max_files
        assert files
        report['files']=files
        for file in files:
            response=client.get('/api/course-source',params={'course':course['id'],'source':file['id']})
            response.raise_for_status();assert response.content
        checks['local_attachments_readable_over_http']=True
        operation('/api/model-settings',{'search_mode':'codex_chatgpt','codex_images':True,'extractor':'basic'})
        first,search=chat('@'+args.course+' '+args.query)
        assert first['questions'],search
        checks['chatgpt_selects_current_query_without_reference']=any(c['provider']=='codex_chatgpt' and c['status']=='ok'
            and c['reference_count']==0 for c in search['model_calls']) and 'chatgpt_selection' in search['method']
        with BankRepository(settings.question_bank_dir/'question_bank.sqlite3',readonly=True) as repo:
            known={q['id'] for q in repo.rows(course['id'])}
        checks['selected_ids_and_sources_are_local']=all(q['id'] in known and q['origins'] and q['course_id']==course['id'] for q in first['questions'])
        images=0
        for q in first['questions']:
            for index in range(len(q['images'])):
                response=client.get('/api/question-image',params={'id':q['id'],'index':index});response.raise_for_status()
                assert response.content;images+=1
        checks['selected_original_question_images_readable']=images>0
        again,similar=chat('以第一题为参照，再找一道同类型题')
        checks['chatgpt_reference_followup']=any(c['provider']=='codex_chatgpt' and c['status']=='ok'
            and c['reference_count']==1 for c in similar.get('model_calls',[])) and 'chatgpt_similar' in similar.get('method','')
        checks['no_answers_or_personal_material']=all(not r['profile'] and not r['style'] and all('answers' not in q for q in r['questions']) for r in (first,again))
        before=get('/api/history',params={'session':session})
        stop();start()
        restored=get('/api/history',params={'session':session})
        checks['session_and_results_survive_service_restart']=restored['session']['question_course']==course['id'] and [
            q['id'] for q in restored['turns'][0]['reply']['questions']]==[q['id'] for q in before['turns'][0]['reply']['questions']]
        checks['chatgpt_preferences_survive_restart']=get('/api/model-settings')['search_mode']=='codex_chatgpt'
        after=get('/api/course-status',params={'course':course['id']})
        checks['files_and_job_survive_restart']=after['coverage']==status['coverage'] and after['jobs'][0]['id']==job['job_id']
        response=client.get('/api/course-source',params={'course':course['id'],'source':files[0]['id']})
        checks['retained_attachment_readable_after_restart']=response.status_code==200 and bool(response.content)
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
    finally:
        stop()
        report['passed']=bool(checks) and all(checks.values()) and 'error' not in report
        report['seconds']=time.monotonic()-started
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'report':str(report_path),'passed':report['passed'],'checks':checks,
            'error':report.get('error'),'seconds':report['seconds']},ensure_ascii=False,indent=2),flush=True)
    assert report['passed'],report.get('error',checks)


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
