import json
import threading
import time

import httpx
import pytest

from aitown.config import Settings
from aitown.models import AnswerPayload,Decision,ReviewPayload
from aitown.runtime.checkpoints import TurnPaused
from aitown.ui.demo import settings_for_ui
from aitown.ui.server import make_server
from aitown.ui.service import UIError,UIService


@pytest.fixture
def ui(tmp_path):
    cfg=settings_for_ui(Settings(),tmp_path)
    service=UIService(cfg,tmp_path/'ui.sqlite3')
    try:yield service
    finally:service.close()


def session(ui,scope='synthetic'):
    return ui.call('_create_session',{'scope':scope})['id']


def completed(ui,method,*args):
    key=ui.start_job(method,*args)['job_id']
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        result=ui.job(key)
        if result['status']!='running':return result
        time.sleep(.01)
    raise AssertionError('UI job did not complete')


def ask(ui,sid,question):
    result=completed(ui,'_chat',{'session':sid,'question':question})
    assert result['status']=='complete',result
    return result['result']['reply']


def test_sessions_restart_keep_history_and_persistent_skill_switch(ui):
    one=session(ui);two=session(ui)
    assert '42' in ask(ui,one,'计算6*7')['answer']
    assert len(ui.call('_history',one)['turns'])==1
    assert ui.call('_history',two)['turns']==[]
    ui.call('_skill',{'name':'calculator','enabled':False})
    failed=ask(ui,one,'计算6*7')
    assert failed['stop_reason']=='skill_failed' and not failed['skills'][0]['ok']
    # Re-open all Agent/SQLite resources on the owning worker.
    def reopen():
        ui.agent.close();ui.meta.close();ui._start()
    ui.pool.submit(reopen).result()
    assert len(ui.call('_history',one)['turns'])==2
    assert not next(s for s in ui.call('_bootstrap')['skills'] if s['name']=='calculator')['enabled']


def test_feedback_correction_retrieves_new_value_and_rejects_cross_session(ui):
    sid=session(ui);other=session(ui)
    reply=ask(ui,sid,'我喜欢咖啡还是茶？')
    assert reply['sources'] and '茶' in reply['answer']
    turn=ui.call('_history',sid)['turns'][0]
    with pytest.raises(UIError):ui.call('_feedback',{'session':other,'turn_id':turn['id'],'kind':'like_me'})
    ui.call('_feedback',{'session':sid,'turn_id':turn['id'],'kind':'unlike_me','comment':'合成评价'})
    facts=ui.call('_facts',sid);fid=facts[0]['id']
    saved=completed(ui,'_correct',{'session':sid,'turn_id':turn['id'],'fact_id':fid,'value':'喜欢温水'})
    assert saved['status']=='complete' and saved['result']['saved']
    assert '温水' in ask(ui,sid,'我现在喜欢喝什么？')['answer']
    assert ui.call('_history',sid)['turns'][0]['reply']['source_changed']
    feedback=ui.call('_history',sid)['turns'][0]['feedback']
    assert [f['kind'] for f in feedback]==['unlike_me','fact_correction']
    blank=session(ui,'');wrong=completed(ui,'_correct',{'session':blank,'turn_id':turn['id'],'fact_id':saved['result']['fact_id'],'value':'不应保存'})
    assert wrong['status']=='failed'


def test_import_update_delete_changes_results_and_removes_stale_debug(ui):
    sid=session(ui)
    imported=completed(ui,'_import',{'name':'鹭湖资料.md','content':'鹭湖图书角收藏571本书。'})
    assert imported['status']=='complete'
    reply=ask(ui,sid,'鹭湖图书角收藏多少本书？')
    assert '571' in reply['answer']
    doc=imported['result']['id']
    updated=completed(ui,'_import',{'name':'鹭湖资料.md','content':'鹭湖图书角收藏572本书。'})
    assert updated['result']['id']==doc
    assert '572' in ask(ui,sid,'鹭湖图书角收藏多少本书？')['answer']
    completed(ui,'_delete_document',doc)
    old=ui.call('_history',sid)['turns']
    assert all(t['reply']['source_changed'] for t in old)
    assert '571' not in json.dumps([t['reply'] for t in old],ensure_ascii=False)
    assert '572' not in json.dumps([t['reply'] for t in old],ensure_ascii=False)
    assert doc not in [d['id'] for d in ui.call('_maintenance',sid)['documents']]
    assert completed(ui,'_import',{'name':'../original.html','content':'not allowed'})['status']=='failed'


def test_scope_profile_and_facts_are_separate_from_evidence(ui):
    sid=session(ui);blank=session(ui,'')
    maintenance=ui.call('_maintenance',sid)
    assert maintenance['profile'] and maintenance['facts']
    assert ui.call('_maintenance',blank)['profile']==[]
    assert ask(ui,blank,'我喜欢喝什么？')['stop_reason']=='no_evidence'
    reply=ask(ui,sid,'青竹活动室容量是多少？')
    assert reply['sources'] and reply['profile']
    assert all(source['source'].startswith('document:') for source in reply['sources'])
    assert all(source['source'].startswith('trait:') for source in reply['profile'])


def test_pending_recovery_and_injected_model_review_are_actual_agent_calls(ui):
    sid=session(ui)
    def pause():
        with pytest.raises(TurnPaused):ui.agent.ask('计算6*7',sid,scope='synthetic',interrupt_after=['skill'])
    ui.pool.submit(pause).result()
    assert ui.call('_history',sid)['pending']
    resumed=completed(ui,'_resume',sid)
    assert resumed['status']=='complete' and '42' in resumed['result']['reply']['answer']
    assert len(ui.call('_history',sid)['turns'])==1
    class Model:
        def structured(self,messages,schema):
            if schema is Decision:return Decision(intent='knowledge',query='青竹活动室容量是多少？',plan=['rag'])
            if schema is AnswerPayload:return AnswerPayload(answer='999人',source_refs=['invented#9'])
            return ReviewPayload(answer='9人',source_refs=['document:1:0#0'],corrections=['修正错误容量'])
        def close(self):pass
    ui.pool.submit(lambda:setattr(ui.agent,'model',Model())).result()
    reply=ask(ui,sid,'青竹活动室容量是多少？')
    assert reply['answer']=='9人' and reply['debug']['draft']=='999人'
    assert reply['debug']['reviews'][0]['changed']


def test_busy_jobs_report_phase_and_do_not_accept_duplicate_writes(ui):
    entered=threading.Event();release=threading.Event()
    def slow():
        ui._progress('整理资料');entered.set();release.wait(2);return {'saved':True}
    ui.slow=slow
    key=ui.start_job('slow')['job_id'];assert entered.wait(1)
    try:
        assert ui.job(key)['phase']=='整理资料'
        with pytest.raises(UIError):ui.start_job('_refresh')
        with pytest.raises(UIError):ui.call('_bootstrap')
    finally:release.set()
    while ui.job(key)['status']=='running':time.sleep(.01)


def test_http_loopback_origin_json_and_static_paths(ui):
    server=make_server(ui,0);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    try:
        with httpx.Client(base_url=origin,trust_env=False) as client:
            assert client.get('/').status_code==200
            assert client.get('/app.js').status_code==200
            assert client.get('/data/processed/wechat.html').status_code==404
            assert client.get('/api/bootstrap',headers={'Host':'attacker.invalid'}).status_code==403
            assert client.post('/api/session',json={},headers={'Origin':'https://attacker.invalid'}).status_code==403
            assert client.post('/api/session',content='scope=synthetic',headers={'Content-Type':'application/x-www-form-urlencoded'}).status_code==415
            assert client.post('/api/session',json={'scope':'synthetic'},headers={'Origin':origin}).status_code==200
            assert client.get('/').headers['content-security-policy'].startswith("default-src 'self'")
            assert 'access-control-allow-origin' not in client.get('/').headers
    finally:server.shutdown();server.server_close();worker.join()


def test_live_mode_failure_is_labelled_program_result_not_model_answer(ui):
    from aitown.errors import AgentError
    class FailedModel:
        def structured(self,*args):raise AgentError('model','draft','unavailable','本机模型不可用')
        def close(self):pass
    def fail():
        ui.agent.model=FailedModel()
        ui.agent.settings=ui.agent.settings.model_copy(update={'mode':'live','api_key':'synthetic-test','model':'test'})
    ui.pool.submit(fail).result()
    reply=ask(ui,session(ui),'青竹活动室容量是多少？')
    assert reply['mode']=='live' and reply['stop_reason']=='model_failed'
    assert reply['output_kind']=='program_result'
