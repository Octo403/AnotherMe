"""Exercise real upload/URL workers, optional ChatGPT and restart in an isolated bank."""
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


PUBLIC_PAPER = 'https://raw.githubusercontent.com/victkk/FDU-Sharing/master/public/resources/%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD%E7%9A%84%E8%BD%AF%E4%BB%B6%E5%9F%BA%E7%A1%80/2026%E6%98%A5-%E6%9C%9F%E6%9C%AB-%E8%AF%95%E9%A2%98.md'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--url',default=PUBLIC_PAPER)
    parser.add_argument('--chatgpt',action='store_true')
    parser.add_argument('--preview-port',type=int,default=0)
    args=parser.parse_args();run_id=uuid.uuid4().hex
    run=(Path('data/runtime/course-supplements')/run_id).resolve();run.mkdir(parents=True)
    report_path=Path('data/evaluation/question_bank')/f'course-supplements-{run_id}.json'
    installed=Settings.from_env(mode='demo')
    settings=settings_for_ui(installed.model_copy(update={'question_bank_dir':run/'bank',
        'question_similar_provider':'codex_chatgpt' if args.chatgpt else 'none','question_similar_model':'',
        'question_extractor':'basic','question_pdf_ocr':'none','mineru_api_key':''}),run/'app')
    service=server=thread=client=None;checks={};samples=[];stages=[];started=time.monotonic()
    report={'run':str(run),'source':str(args.source),'url':args.url,'checks':checks,'samples':samples,'stages':stages,
        'note':'独立新库，用户提供 MD 的副本、生成的单题 PDF 和真实公开 MD 网址；实际 HTTP、多进程处理、BGE、重启。ChatGPT 按参数开启，不写主库。'}
    course='人工智能的软件基础'

    def start(port=0):
        nonlocal service,server,thread,client
        service=UIService(settings,run/'ui.sqlite3');server=make_server(service,port)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        client=httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False,timeout=30)
    def stop():
        nonlocal service,server,thread,client
        if client:client.close();client=None
        if server:server.shutdown();server.server_close();thread.join();server=None
        if service:service.close();service=None
    def get(path,**kw):
        response=client.get(path,**kw);response.raise_for_status();return response.json()
    def post(path,payload):
        response=client.post(path,json=payload);response.raise_for_status();return response.json()
    def wait_course(expected):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            status=get('/api/course-status',params={'course':course})
            snapshot=[(j['id'],j['status'],j['stage']) for j in status['jobs']]
            if not stages or stages[-1]!=snapshot:stages.append(snapshot);print(json.dumps({'jobs':snapshot}),flush=True)
            if len(status['files'])>=expected and not any(j['status'] in ('queued','running') for j in status['jobs']) and all(f['status'] not in ('discovered','downloaded','processing') for f in status['files']):return status
            time.sleep(.3)
        raise TimeoutError('补充资料后台处理超时')
    try:
        import fitz
        pdf=fitz.open();page=pdf.new_page();page.insert_text((50,70),'1. Calculate the rank of matrix A = [[1, 0], [0, 1]].')
        pdf_bytes=pdf.tobytes();pdf.close();(run/'matrix.pdf').write_bytes(pdf_bytes)
        start();session=post('/api/session',{})['id']
        response=client.post('/api/course-upload',data={'course':course,'session':session,'replace':'false'},files=[
            ('files',(args.source.name,args.source.read_bytes(),'text/markdown')),('files',('matrix.pdf',pdf_bytes,'application/pdf'))])
        response.raise_for_status();uploaded=response.json();report['uploaded']=uploaded
        checks['multipart_creates_course_and_two_retained_sources']=len(uploaded['items'])==2 and uploaded['job'] is not None
        linked=post('/api/course-urls',{'course':course,'session':session,'urls':[args.url]});report['linked']=linked
        checks['url_addition_during_worker_is_durably_queued']=bool(linked['job'].get('queued'))
        status=wait_course(3)
        failed=[f for f in status['files'] if f['status']=='failed']
        if failed:
            report['retry_attempts']=[]
            for file in failed:
                retry=post('/api/course-retry',{'course':course,'source':file['id']})
                report['retry_attempts'].append({'source':file['id'],'initial_error':file['error'],'job':retry})
                status=wait_course(3)
        report['status']=status
        checks['both_workers_finish_without_file_errors']=all(f['status'] in ('ready','partial') and not f['error'] for f in status['files'])
        checks['raw_uploads_and_url_are_retained']=status['coverage']['raw_retained']==3
        checks['url_download_keeps_markdown_format']=next(f for f in status['files'] if f['origin_kind']=='url')['name'].endswith('.md')
        for item,content in zip(uploaded['items'],[args.source.read_bytes(),pdf_bytes]):
            response=client.get('/api/course-source',params={'course':course,'source':item['id']});response.raise_for_status()
            assert response.content==content
        checks['http_originals_match_uploaded_bytes']=True
        duplicate=client.post('/api/course-upload',data={'course':course},files={'files':(args.source.name,args.source.read_bytes())});duplicate.raise_for_status()
        checks['duplicate_import_reuses_existing_source']=duplicate.json()['items'][0]['reused'] and duplicate.json()['job'] is None
        duplicate_url=post('/api/course-urls',{'course':course,'urls':[args.url+'#ignored']})
        checks['duplicate_url_reuses_existing_source']=duplicate_url['items'][0]['reused']
        with BankRepository(settings.question_bank_dir/'question_bank.sqlite3',readonly=True) as repo:
            rows=repo.rows(course);report['questions']=rows
            checks['md_two_major_questions_and_pdf_image_are_searchable']=all(any(row['number']==str(n) and row['source_id']==uploaded['items'][0]['id'] for row in rows) for n in (1,2)) and any(row['source_id']==uploaded['items'][1]['id'] and row['images'] for row in rows)
        for query in ['找残差网络相关的题','找大模型系统软件科普讲座设计的题']:
            key=post('/api/chat',{'session':session,'question':query})['job_id'];deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                result=get('/api/job',params={'id':key})
                if result['status']=='complete':break
                if result['status']=='failed':raise RuntimeError(result['error'])
                time.sleep(.25)
            else:raise TimeoutError('找题超时')
            reply=result['result']['reply'];search=next(s['data'] for s in reply['skills'] if s['name']=='question_search')
            samples.append({'query':query,'questions':reply['questions'],'method':search['method'],'model_calls':search.get('model_calls',[])})
            assert reply['questions'] and not reply['profile'] and not reply['style']
            assert all(q['content_format']=='markdown' and not q['images'] and 'answers' not in q for q in reply['questions'])
        checks['chat_queries_select_expected_major_questions']=[[q['number'] for q in sample['questions']] for sample in samples]==[['1'],['2']]
        if args.chatgpt:checks['actual_chatgpt_second_round']=all(any(c['status']=='ok' and c['provider']=='codex_chatgpt' for c in sample['model_calls']) for sample in samples)
        before=get('/api/history',params={'session':session});stop();start(args.preview_port)
        after=get('/api/history',params={'session':session});restored=get('/api/course-status',params={'course':course})
        checks['history_course_sources_and_questions_survive_restart']=before['turns']==after['turns'] and after['session']['question_course']==course and restored['coverage']['questions']==status['coverage']['questions'] and len(restored['files'])==3
        report['preview_url']=f'http://127.0.0.1:{server.server_port}'
        report['session']=session
    except Exception as exc:report['error']=f'{type(exc).__name__}: {exc}'
    finally:
        report['seconds']=time.monotonic()-started;report['passed']=bool(checks) and all(checks.values()) and 'error' not in report
        report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'report':str(report_path),'passed':report['passed'],'checks':checks,'error':report.get('error'),'seconds':report['seconds'],'preview_url':report.get('preview_url')},ensure_ascii=False,indent=2),flush=True)
        if args.preview_port and report['passed']:
            print('独立预览已启动，Ctrl+C 关闭。',flush=True)
            try:
                while True:time.sleep(.5)
            except KeyboardInterrupt:pass
        stop()
    assert report['passed'],report.get('error',checks)


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8');main()
