"""T12 real local HTTP demonstration on fictional data; no personal DB writes."""
import argparse
import json
import threading
import time
from pathlib import Path

import httpx

from aitown.config import Settings
from aitown.ui.demo import settings_for_ui
from aitown.ui.service import UIService
from aitown.ui.server import make_server
from verify_pipeline import InjectedDraft


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=Path('data/evaluation/t12')/time.strftime('run-%Y%m%d-%H%M%S'))
    parser.add_argument('--port',type=int,default=8502)
    parser.add_argument('--skip-live',action='store_true')
    parser.add_argument('--keep-server',action='store_true')
    args=parser.parse_args();root=args.output;root.mkdir(parents=True,exist_ok=True)
    runtime=Path('data/runtime/t12')/root.name
    cfg=settings_for_ui(Settings.from_env(mode='demo'),runtime)
    service=UIService(cfg,runtime/'ui.sqlite3')
    server=make_server(service,args.port)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    records=[];checks={};screens={}
    client=httpx.Client(base_url=origin,timeout=30,trust_env=False)
    def get(path):
        response=client.get(path);response.raise_for_status();return response.json()
    def post(path,payload):
        response=client.post(path,json=payload);response.raise_for_status();return response.json()
    def job(path,payload):
        key=post(path,payload)['job_id'];phases=[];started=time.perf_counter()
        while True:
            result=get('/api/job?id='+key)
            if result['phase'] not in phases:phases.append(result['phase'])
            if result['status']!='running':break
            time.sleep(.1)
        records.append({'action':path,'phases':phases,'seconds':time.perf_counter()-started,**result})
        save(root/'execution.json',records)
        if result['status']!='complete':raise ValueError(result['error'])
        return result['result']
    def new(title,scope='synthetic'):return post('/api/session',{'title':title,'scope':scope})['id']
    def history(sid):return get('/api/history?session='+sid)
    def ask(sid,question,scene=None):return job('/api/chat',{'session':sid,'question':question,'scene':scene})['reply']
    try:
        one=new('合成演示 · 计算与记忆');two=new('合成演示 · 独立会话')
        first=ask(one,'计算 9 * 18')
        checks['calculator_162']='162' in first['answer'] and first['skills'][0]['data']['value']==162
        checks['offline_label']=first['output_kind']=='offline'
        checks['session_isolation']=history(two)['turns']==[]
        pref=ask(one,'我喜欢咖啡还是茶？')
        checks['fact_and_source']='茶' in pref['answer'] and bool(pref['sources'])
        turn=history(one)['turns'][-1]
        post('/api/feedback',{'session':one,'turn_id':turn['id'],'kind':'unlike_me','comment':'合成演示反馈：回答应更自然'})
        facts=get('/api/maintenance?session='+one)['facts']
        correction=job('/api/correct',{'session':one,'turn_id':turn['id'],'fact_id':facts[0]['id'],'value':'喜欢温水'})
        after=ask(one,'我现在喜欢喝什么？')
        checks['explicit_correction']='温水' in after['answer'] and correction['saved']
        checks['feedback_saved']=len(history(one)['turns'][1]['feedback'])==2
        post('/api/skill',{'name':'calculator','enabled':False})
        disabled=ask(one,'计算6*7')
        checks['disabled_skill']=disabled['stop_reason']=='skill_failed'
        post('/api/skill',{'name':'calculator','enabled':True})
        imported=job('/api/import',{'name':'鹭湖资料.md','content':'合成设定：鹭湖图书角有571本书。'})
        documents=new('合成演示 · 资料维护')
        checks['import_retrieved']='571' in ask(documents,'鹭湖图书角有多少本书？')['answer']
        job('/api/delete-document',{'id':imported['id']})
        checks['deleted_reference_hidden']=history(documents)['turns'][0]['reply']['source_changed']
        job('/api/refresh',{})
        checks['profile_visible']=bool(get('/api/maintenance?session='+one)['profile'])
        # Actual close/re-open, using the same database and skill settings.
        def reopen():service.agent.close();service.meta.close();service._start()
        service.pool.submit(reopen).result()
        checks['history_after_restart']=len(history(one)['turns'])==4
        source_session=new('合成演示 · 事实来源')
        source_reply=ask(source_session,'青竹活动室最多能坐几个人？')
        checks['rag_nine']='9' in source_reply['answer'] and bool(source_reply['sources'])
        followup=ask(source_session,'之前说的需要提前预约吗？')
        checks['context_followup']=bool(followup['sources']) and '两天' in followup['answer'] and bool(followup['debug']['decision'])
        screens['sources']=origin+'/?session='+source_session+'&view=sources'
        if not args.skip_live:
            job('/api/mode',{'mode':'live'})
            live=new('合成演示 · 真实模型对话')
            invite=ask(live,'下课一起去便利店买杯喝的？','agree')
            knowledge=ask(live,'青竹活动室最多能坐几个人？')
            calc=ask(live,'先查青竹活动室的每人材料费，再计算9个人的总费用')
            plan=ask(live,'从我的已整理资料中找出有关分享材料的安排，按时间顺序摘录对应对话')
            unknown=ask(live,'我去过月球吗？')
            checks['live_chat_generated']=invite['output_kind']=='generated' and invite['debug']['decision']['intent']=='chat' and '未查询到' not in invite['answer']
            checks['live_grounded_nine']=knowledge['output_kind']=='generated' and '9' in knowledge['answer']
            checks['live_rag_calculator']=any(s['name']=='calculator' and s['ok'] and s['data'].get('value')==162 for s in calc['skills'])
            checks['live_query_summary']=[s['name'] for s in plan['skills']]==['memory_query','dialogue_summary']
            checks['live_unknown_not_generated']=unknown['stop_reason']=='no_evidence' and unknown['output_kind']=='program_result'
            original=service.agent.model
            service.pool.submit(lambda:setattr(service.agent,'model',InjectedDraft(original,'document:1:0#0'))).result()
            injected=ask(live,'青竹活动室容量是多少？')
            service.pool.submit(lambda:setattr(service.agent,'model',original)).result()
            checks['real_review_corrected_injected_draft']=injected['debug']['draft']=='活动室容量999人，我去过月球。' and '9' in injected['answer'] and '999' not in injected['answer'] and injected['stop_reason']=='answered'
            screens['live']=origin+'/?session='+live+'&view=sources'
        overview=new('新的对话')
        screens['overview']=origin+'/?session='+overview
        screens['maintenance']=screens['overview']+'&settings=1'
        def integrity():
            return {'foreign_key_errors':len(service.agent.memory.conn.execute('PRAGMA foreign_key_check').fetchall()),
                'feedback':service.agent.memory.conn.execute('SELECT count(*) FROM feedback').fetchone()[0],
                'facts':service.agent.memory.conn.execute('SELECT count(*) FROM facts').fetchone()[0],
                'scope':'synthetic','local_provider':service.live_available()}
        result=service.pool.submit(integrity).result()
        checks['foreign_keys']=result['foreign_key_errors']==0
        calls=[call for row in records for call in row.get('result',{}).get('reply',{}).get('debug',{}).get('model_calls',[])]
        report={'checks':checks,'passed':all(checks.values()),'screenshots':screens,'integrity':result,'execution_actions':len(records),
            'model_requests':sum(call['provider_request'] for call in calls),'input_tokens':sum(call.get('input_tokens') or 0 for call in calls),
            'output_tokens':sum(call.get('output_tokens') or 0 for call in calls),
            'note':'Real HTTP and Agent operations on fictional data. Browser control was unavailable; screenshots are separately rendered headlessly.'}
        save(root/'report.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        if args.keep_server:
            print('Synthetic demonstration remains available at '+origin,flush=True)
            while True:time.sleep(1)
    except KeyboardInterrupt:pass
    finally:client.close();server.shutdown();server.server_close();worker.join();service.close()


if __name__=='__main__':main()
