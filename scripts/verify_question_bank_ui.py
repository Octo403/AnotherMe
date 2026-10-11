"""Real HTTP + Agent smoke on public questions and a separate fictional app DB."""
import json
import threading
import time
import tempfile
from pathlib import Path

import httpx

from aitown.config import Settings
from aitown.question_bank.models import GetArgs
from aitown.ui.demo import settings_for_ui
from aitown.ui.server import make_server
from aitown.ui.service import UIService


def main():
    directory=Path('data/runtime/question-bank-ui-check')
    installed=Settings.from_env(mode='demo')
    installed.question_similar_provider='none'  # Live account selection is measured separately.
    settings=settings_for_ui(installed,directory)
    service=UIService(settings,directory/'ui.sqlite3')
    server=make_server(service,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    checks={}
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',timeout=30,trust_env=False) as client:
            def get(path):
                response=client.get(path);response.raise_for_status();return response.json()
            def post(path,payload):
                response=client.post(path,json=payload);response.raise_for_status();return response.json()
            sid=post('/api/session',{'title':'题库实测','scope':''})['id']
            def ask(text):
                key=post('/api/chat',{'session':sid,'question':text})['job_id']
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    result=get('/api/job?id='+key)
                    if result['status']!='running':
                        assert result['status']=='complete',result
                        return result['result']['reply']
                    time.sleep(.1)
                raise TimeoutError('UI 题库查询超时')
            count=lambda:service.agent.runtime_repository.conn.execute('SELECT count(*) FROM facts').fetchone()[0]
            before=service.pool.submit(count).result()
            first=ask('找集合与图论有关等价关系的题，先别给答案，找两道')
            questions=first['questions']
            checks['two_cards']=len(questions)==2
            checks['local_bge']=first['skills'][0]['data']['method']=='bge+keyword+bge_rerank'
            checks['no_answers']=all('answers' not in q for q in questions)
            checks['external_sources']=all(s['external_url'] and not s['source_ids'] for s in first['sources'])
            second=ask('把第二题完整给我')
            checks['ordinal_binding']=second['questions'][0]['id']==questions[1]['id']
            original=service.pool.submit(lambda:service.agent.question_bank.get(
                GetArgs(question_ids=[questions[1]['id']])).results[0]).result()
            checks['question_text_hidden']=all('text' not in q for q in questions+second['questions']) and original['text'] not in json.dumps(second,ensure_ascii=False)
            checks['original_preserved']=second['questions'][0]['url']==original['url'] and second['questions'][0]['images']==original['images']
            imageq=next((q for q in questions if q['images']),None)
            if imageq:
                response=client.get(f'/api/question-image?id={imageq["id"]}&index=0')
                checks['image_http']=response.status_code==200 and response.headers['content-type']=='image/png' and len(response.content)>100
            status=get('/api/course-status')
            checks['partial_coverage']=status['status']=='partial' and status['coverage']['questions']>0
            paired=ask('找集合与图论2023年期末关于幂集的证明题，先别给答案')
            cards=paired['questions']
            chosen=next(n for n,q in enumerate(cards,1) if q['answer_available'])
            checks['paired_answers_hidden_in_search']=all('answers' not in q for q in cards)
            shown=ask(f'现在给我第{chosen}题已有的答案和出处')
            answer=shown['questions'][0]['answers'][0]
            checks['paired_answers_opt_in']=bool(answer['text']) and answer['text'] in shown['answer']
            checks['answer_locator_and_url']=answer['locations']==[55] and answer['location_kind']=='paragraph' and bool(answer['url'])
            hints=ask('找集合与图论2023年期末完美二叉树的证明题，先别给答案')
            chosen=next(n for n,q in enumerate(hints['questions'],1) if q['answer_available'])
            hinted=ask(f'现在给我第{chosen}题已有的答案和出处')
            hint=hinted['questions'][0]['answers'][0]
            checks['incomplete_answer_warning']=hint['status']=='partial' and bool(hint['missing']) and '答案缺口' in hinted['answer']
            checks['no_fact_write']=service.pool.submit(count).result()==before
            # Start the actual independent CLI worker on a cached fictional
            # course file. It shares neither this app DB nor the public bank.
            from aitown.question_bank.models import BankConfig, COURSE_ID, Source
            from aitown.question_bank.repository import BankRepository
            from aitown.question_bank.scheduler import launch
            from aitown.question_bank.jobs import pid_alive
            from aitown.question_bank.semantic import index_directory
            with tempfile.TemporaryDirectory(prefix='anotherme-course-worker-') as temp:
                worker_config=BankConfig(root=Path(temp))
                with BankRepository(worker_config.database) as repo:
                    repo.save_discovery([{'id':COURSE_ID,'name':'集合与图论','path':'synthetic/course'}],[])
                    with repo.conn:
                        sourceid=repo.save_source(Source(course_id=COURSE_ID,repository='synthetic/fixture',
                            path='synthetic.txt',url='https://example.org/synthetic.txt'))
                    raw=worker_config.root/'raw'/str(sourceid)/'synthetic.txt';raw.parent.mkdir(parents=True)
                    raw.write_text('1. 证明等价关系的合成条件。',encoding='utf-8')
                started=time.perf_counter();job=launch(worker_config)
                checks['worker_returns_immediately']=time.perf_counter()-started<5
                checks['ui_responsive_during_worker']=get('/api/course-status')['coverage']['questions']>0
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    with BankRepository(worker_config.database,readonly=True) as repo:
                        row=repo.conn.execute('SELECT * FROM jobs WHERE id=?',(job['job_id'],)).fetchone()
                        complete=row['status']=='complete' and repo.coverage(COURSE_ID)['questions']==1
                    if complete and (index_directory(worker_config,COURSE_ID)/'questions.faiss').is_file() and not pid_alive(job['worker_pid']):break
                    time.sleep(.2)
                checks['actual_worker_and_index']=complete and (index_directory(worker_config,COURSE_ID)/'questions.faiss').is_file()
            target=Path('data/evaluation/question_bank/ui.json');target.parent.mkdir(parents=True,exist_ok=True)
            report={'checks':checks,'passed':all(checks.values()),'question_ids':[q['id'] for q in questions],
                'coverage':status['coverage'],'note':'真实 HTTP / Agent；用户界面隐藏题目识别文本，保留原图与来源。浏览器视觉验收仍待办，不能由 HTTP 检查代替；题图/原页接口另见 images.json'}
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False,indent=2))
            assert all(checks.values())
    finally:
        server.shutdown();server.server_close();thread.join();service.close()


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
