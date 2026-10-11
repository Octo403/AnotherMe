"""Exercise the actual local UI with private source-backed cases, never seed facts."""
import argparse
import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import httpx

from aitown.config import Settings


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def read_tables(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        return {name:conn.execute('SELECT * FROM '+name+' ORDER BY id').fetchall()
            for name in ['facts','memories','message_refs','style_examples','chat_turns']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cases',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8501)
    parser.add_argument('--directory',type=Path,default=Path('data/runtime/ui-wechat'))
    args=parser.parse_args();root=args.output.resolve()
    if not root.is_relative_to(Path('data/private').resolve()):raise ValueError('真实微信演示正文与截图须保存到 data/private。')
    root.mkdir(parents=True,exist_ok=True)
    installed=Settings.from_env(mode='demo');before=read_tables(installed.retrieval_database)
    cases=json.loads(args.cases.read_text(encoding='utf-8'));origin=f'http://127.0.0.1:{args.port}'
    records=[];checks={};sessions={};screens={};all_sources=[]
    with httpx.Client(base_url=origin,trust_env=False,timeout=30) as client:
        def get(path):
            r=client.get(path);r.raise_for_status();return r.json()
        def post(path,data):
            r=client.post(path,json=data);r.raise_for_status();return r.json()
        bootstrap=get('/api/bootstrap')
        if bootstrap['data']!='wechat' or bootstrap['mode']!='live':raise ValueError('请先启动真实本机模型的微信副本界面。')
        checks['wechat_copy_live']=True
        checks['retrieval_initialized']=not bootstrap['retrieval_error']
        for case in cases:
            group=case.get('session',case['id']);scope=case.get('scope',bootstrap['default_scope'])
            if group not in sessions:sessions[group]=post('/api/session',{'title':case['title'],'scope':scope})['id']
            sid=sessions[group];started=time.perf_counter();phases=[]
            key=post('/api/chat',{'session':sid,'question':case['question'],'scene':case.get('scene')})['job_id']
            while True:
                result=get('/api/job?id='+key)
                if result['phase'] not in phases:phases.append(result['phase'])
                if result['status']!='running':break
                if time.perf_counter()-started>180:raise TimeoutError('本机模型操作超时，请查看界面当前状态。')
                time.sleep(.15)
            row={'case':case['id'],'question':case['question'],'scope':scope,'session':sid,
                 'seconds':time.perf_counter()-started,'phases':phases,'result':result}
            records.append(row);save(root/'execution.json',records)
            if result['status']!='complete':checks[case['id']]=False;continue
            reply=result['result']['reply'];expect=case['expect'];outcomes={}
            outcomes['stop']=reply['stop_reason'] in expect.get('stops',['answered'])
            if expect.get('generated'):outcomes['generated']=reply['output_kind']=='generated'
            if expect.get('no_generation'):outcomes['no_generation']=reply['output_kind']=='program_result'
            for i,pattern in enumerate(expect.get('patterns',[])):outcomes['pattern_'+str(i)]=bool(re.search(pattern,reply['answer']))
            if expect.get('evidence'):outcomes['evidence']=bool(reply['sources']) and any(s['evidence'] for s in reply['sources'])
            if expect.get('memory_id'):outcomes['memory_id']=any(s['source']=='memory:'+str(expect['memory_id']) for s in reply['sources'])
            if expect.get('reference'):outcomes['reference']=any(r['selected'] and r['turn_ids'] for r in reply['debug'].get('references',[]))
            if expect.get('semantic_backend'):
                with closing(sqlite3.connect((args.directory/'application.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as conn:
                    stored=json.loads(conn.execute('SELECT reply_json FROM runtime_runs WHERE id=?',(reply['request_id'],)).fetchone()[0])
                row['retrieval_backend']=stored.get('retrieval_details',{}).get('backend','')
                outcomes['semantic_backend']=row['retrieval_backend'].startswith(expect['semantic_backend'])
            if expect.get('skills'):outcomes['skills']=[s['name'] for s in reply['skills']]==expect['skills'] and all(s['ok'] for s in reply['skills'])
            if expect.get('bound_count'):
                summary=next((s for s in reply['skills'] if s['name']=='dialogue_summary'),None)
                calculator=next((s for s in reply['skills'] if s['name']=='calculator'),None)
                outcomes['bound_count']=bool(summary and calculator and calculator['data'].get('value')==summary['data']['count']*expect['bound_count'])
            if expect.get('style'):outcomes['style']=bool(reply['style']) and bool(reply['profile'])
            row['checks']=outcomes;checks[case['id']]=all(outcomes.values());all_sources.extend(reply['sources'])
            if case.get('screenshot'):screens[case['screenshot']]=origin+'/?session='+sid+'&view=sources'
            print(case['id']+': '+str(checks[case['id']]),flush=True)
        save(root/'execution.json',records)
        primary=sessions[cases[0].get('session',cases[0]['id'])]
        maintenance=get('/api/maintenance?session='+primary);save(root/'maintenance.json',maintenance)
        checks['wechat_memories_visible']=bool(maintenance['memories'])
        checks['synthetic_documents_absent']=maintenance['documents']==[]
        checks['session_history']=all(len(get('/api/history?session='+sid)['turns'])==sum(row['session']==sid for row in records) for sid in sessions.values())
        screens['maintenance']=origin+'/?session='+primary+'&settings=1'
        # Inspect persisted state directly, with the original database read-only.
        with closing(sqlite3.connect((args.directory/'application.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            approved=True
            for source in all_sources:
                for evidence in source['evidence']:
                    row=conn.execute('SELECT review_status,status,conversation_id FROM message_refs WHERE id=?',(evidence['id'],)).fetchone()
                    approved=approved and bool(row and row['review_status']=='approved' and row['status'] in ('text','structured_text'))
            checks['cleaned_evidence_approved']=approved and bool(all_sources)
            checks['no_general_documents']=conn.execute("SELECT count(*) FROM knowledge_documents WHERE purpose='knowledge'").fetchone()[0]==0
            checks['foreign_keys']=conn.execute('PRAGMA foreign_key_check').fetchall()==[]
            counts={table:conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ['message_refs','memories','style_examples','facts','chat_turns']}
        after=read_tables(installed.retrieval_database)
        checks['formal_database_unchanged']=before==after
        checks['wechat_sources_unchanged']=all(before[name]==read_tables(args.directory/'application.sqlite3')[name] for name in ['facts','memories','message_refs','style_examples'])
        calls=[call for row in records for call in row['result'].get('result',{}).get('reply',{}).get('debug',{}).get('model_calls',[])]
        report={'passed':all(checks.values()),'checks':checks,'screenshots':screens,'counts':counts,
                'model_requests':sum(c['provider_request'] for c in calls),'input_tokens':sum(c.get('input_tokens') or 0 for c in calls),
                'output_tokens':sum(c.get('output_tokens') or 0 for c in calls),
                'data':'private reviewed WeChat copy','mode':'live local Qwen','scope':bootstrap['default_scope'],
                'interactive_browser_control':False,'note':'Read-only source demo; no fabricated owner ratings, corrections or personal facts.'}
        save(root/'report.json',report);print(json.dumps({'passed':report['passed'],'checks':checks,'counts':counts,'model_requests':report['model_requests']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
