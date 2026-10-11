import json
import os
import threading
import time
from contextlib import closing

import httpx
import pytest

from aitown.config import Settings
from aitown.providers import ProviderConfig
from aitown.ui.demo import settings_for_ui
from aitown.ui.model_settings import ModelPreferences, api_operation
from aitown.ui.service import UIError, UIService
from aitown.ui.server import make_server


def preferences(**changes):
    data={'search_mode':'api','api':{'provider':'deepseek','base_url':'https://api.deepseek.com',
        'model':'user-selected-exact-model','api_key':'synthetic-llm-secret'},
        'mineru_token':'synthetic-mineru-secret','remember_keys':False}
    data.update(changes)
    return data


def test_settings_apply_only_to_course_and_secrets_do_not_serialize(tmp_path):
    config=settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'data')
    metadata=tmp_path/'ui.sqlite3'
    with closing(UIService(config,metadata)) as service:
        public=service.call('_save_model_settings',preferences())
        assert public['api']['model']=='user-selected-exact-model' and public['api']['has_key']
        assert public['mineru_has_token']
        assert 'synthetic-llm-secret' not in json.dumps(public)
        assert 'synthetic-mineru-secret' not in json.dumps(public)
        current=service.pool.submit(lambda:(service.agent.question_bank.config.api.api_key,
            service.agent.settings.base_url,service.agent.model)).result()
        assert current==('synthetic-llm-secret',config.base_url,None)
        with pytest.raises(UIError):service.call('_mode','live')
        saved=service.pool.submit(lambda:service.meta.execute("SELECT value FROM ui_settings WHERE key='model_preferences'").fetchone()[0]).result()
        assert 'synthetic-llm-secret' not in saved and 'synthetic-mineru-secret' not in saved
        blank=preferences();blank['api']['api_key']='';blank['mineru_token']=''
        service.call('_save_model_settings',blank)
        assert service.pool.submit(lambda:service.model_preferences.api.api_key).result()=='synthetic-llm-secret'
        changed=preferences();changed['api']['api_key']='';changed['api']['base_url']='https://other.example/v1'
        with pytest.raises(UIError):service.call('_save_model_settings',changed)
        assert service.call('_model_settings')['api']['base_url']=='https://api.deepseek.com'
    with closing(UIService(config,metadata)) as service:
        restored=service.call('_model_settings')
        assert not restored['api']['has_key'] and restored['search_mode']=='none'


def test_ui_startup_preserves_explicit_course_credentials_without_serializing_them(tmp_path):
    installed=Settings(question_similar_provider='api',question_api=ProviderConfig(model='test',api_key='env-key'),
        mineru_api_key='env-mineru-token')
    for data in ('synthetic','configured'):
        config=settings_for_ui(installed,tmp_path/data,data=data)
        assert config.question_api.api_key=='env-key' and config.mineru_api_key=='env-mineru-token'
        assert 'env-key' not in config.model_dump_json() and 'env-mineru-token' not in config.model_dump_json()


@pytest.mark.skipif(os.name!='nt',reason='Windows current-user DPAPI')
def test_windows_encrypted_keys_survive_restart_without_plaintext(tmp_path):
    config=settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'data');metadata=tmp_path/'ui.sqlite3'
    with closing(UIService(config,metadata)) as service:
        public=service.call('_save_model_settings',preferences(remember_keys=True,extractor='mineru_api'))
        assert public['mineru_token_storage']=='windows_encrypted'
        # A later partial save must not silently opt out of persistence or reset
        # the parser. This also covers blank fields retaining encrypted secrets.
        public=service.call('_save_model_settings',{'codex_images':False})
        assert public['remember_keys'] and public['extractor']=='mineru_api'
        saved=service.pool.submit(lambda:service.meta.execute("SELECT value FROM ui_settings WHERE key='model_preferences'").fetchone()[0]).result()
        assert 'protected_api_key' in saved and 'synthetic-llm-secret' not in saved and 'synthetic-mineru-secret' not in saved
    with closing(UIService(config,metadata)) as service:
        assert service.call('_model_settings')['search_mode']=='api'
        assert service.pool.submit(lambda:service.model_preferences.api.api_key).result()=='synthetic-llm-secret'
        assert service.pool.submit(lambda:service.model_preferences.mineru_token).result()=='synthetic-mineru-secret'
        assert service.call('_model_settings')['mineru_token_storage']=='windows_encrypted'


def test_session_only_token_reports_that_restart_will_clear_it(tmp_path):
    config=settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'data');metadata=tmp_path/'ui.sqlite3'
    with closing(UIService(config,metadata)) as service:
        result=service.call('_save_model_settings',preferences())
        assert result['mineru_token_storage']=='session'
    with closing(UIService(config,metadata)) as service:
        assert service.call('_model_settings')['mineru_token_storage']=='missing'


@pytest.mark.parametrize('url',['https://api.openai.com/v1','http://127.0.0.1.evil.example/v1',
    'http://user:password@localhost/v1','file:///private','http://localhost/v1?token=secret'])
def test_personal_model_rejects_non_loopback_or_embedded_credentials(tmp_path,url):
    config=settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'data')
    with closing(UIService(config,tmp_path/'ui.sqlite3')) as service:
        with pytest.raises(ValueError):service.call('_save_local_model',{'base_url':url,'model':'test'})
        assert not service.call('_bootstrap')['live_available']


def test_local_personal_model_configure_probe_enable_and_restart(tmp_path,monkeypatch):
    from pydantic import BaseModel
    class Probe(BaseModel):ok:bool
    received=[]
    def handle(request):
        received.append(request)
        assert request.url.host=='127.0.0.1'
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':'local-exact'}]})
        data=json.loads(request.content)
        assert data['model']=='local-exact'
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}]})
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    config=settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'data');metadata=tmp_path/'ui.sqlite3'
    payload={'base_url':'http://127.0.0.1:11434/v1','model':'local-exact','remember_key':False}
    with closing(UIService(config,metadata)) as service:
        assert not service.call('_bootstrap')['live_available']
        assert service.call('_local_model_operation',payload,'models')['models']==['local-exact']
        assert service.call('_local_model_operation',payload,'test')['ok']
        service.call('_save_local_model',payload)
        assert service.call('_bootstrap')['live_available']
        service.call('_mode','live')
        assert service.pool.submit(lambda:service.agent.model.structured([{'role':'user','content':'合成测试'}],Probe)).result().ok
        # Updating the model while live rebuilds its client, keeping the course
        # provider and skill selections independent.
        service.call('_save_local_model',{'base_url':'http://localhost:11434/v1','model':'updated-local'})
        assert service.pool.submit(lambda:service.agent.model.settings.model).result()=='updated-local'
        assert service.call('_model_settings')['search_mode']=='none'
    with closing(UIService(config,metadata)) as service:
        assert service.call('_bootstrap')['live_available']
        assert service.call('_local_model_settings')['model']=='updated-local'
    assert len(received)==3


@pytest.mark.skipif(os.name!='nt',reason='Windows current-user DPAPI')
def test_local_model_key_encrypted_and_endpoint_bound(tmp_path):
    config=settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'data');metadata=tmp_path/'ui.sqlite3'
    payload={'base_url':'http://127.0.0.1:11434/v1','model':'local-model','api_key':'synthetic-local-secret','remember_key':True}
    with closing(UIService(config,metadata)) as service:
        public=service.call('_save_local_model',payload)
        assert public['key_storage']=='windows_encrypted' and 'synthetic-local-secret' not in json.dumps(public)
        saved=service.pool.submit(lambda:service.meta.execute("SELECT value FROM ui_settings WHERE key='local_model_preferences'").fetchone()[0]).result()
        assert 'synthetic-local-secret' not in saved
    with closing(UIService(config,metadata)) as service:
        assert service.pool.submit(lambda:service.local_model_preferences.api_key).result()=='synthetic-local-secret'
        service.call('_save_local_model',{'model':'updated-model'})
        assert service.call('_local_model_settings')['key_storage']=='windows_encrypted'
        service.call('_save_local_model',{'base_url':'http://127.0.0.1:11435/v1'})
        assert not service.call('_local_model_settings')['has_key']


def test_model_discovery_without_model_id_and_synthetic_connection_probe(monkeypatch):
    requests=[]
    def handle(request):
        requests.append(request)
        if request.method=='GET':
            assert request.url.path=='/models'
            return httpx.Response(200,json={'data':[{'id':'exact-provider-model'}]})
        payload=json.loads(request.content)
        assert payload['model']=='exact-provider-model'
        assert '合成连接测试' in json.dumps(payload,ensure_ascii=False)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}]})
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    config=ProviderConfig(api_key='synthetic-key')
    assert api_operation(config,'models')['models']==['exact-provider-model']
    config.model='exact-provider-model'
    assert api_operation(config,'test')['ok']
    assert len(requests)==2


@pytest.mark.parametrize('body', [[], {'data': {}}, {'data': []}])
def test_model_directory_errors_do_not_become_empty_dropdown(monkeypatch, body):
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json=body)),**kwargs))
    with pytest.raises(ValueError,match='型号'):
        api_operation(ProviderConfig(api_key='synthetic-key'),'models')


def test_saved_profile_keys_bound_to_endpoint_and_clear_controls(tmp_path):
    config=settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'data')
    with closing(UIService(config,tmp_path/'ui.sqlite3')) as service:
        service.call('_save_model_settings',preferences())
        cleared=preferences(extractor='mineru_api',clear_api_key=True,clear_mineru_token=True)
        cleared['api']['api_key']='';cleared['mineru_token']=''
        result=service.call('_save_model_settings',cleared)
        assert not result['api']['has_key'] and not result['mineru_has_token']
        assert result['search_mode']=='none' and result['extractor']=='basic'


@pytest.mark.parametrize('url',['http://remote.example/v1','https://user:key@remote.example/v1',
    'https://remote.example/v1?key=secret','file:///private'])
def test_unsafe_key_transports_rejected(url):
    with pytest.raises(ValueError):ProviderConfig(base_url=url)


def test_http_settings_to_model_search_history_and_origin_protection(tmp_path,monkeypatch):
    from aitown.question_bank.models import COURSE_ID, Page, ParsedSource, Question, Source
    from aitown.question_bank.repository import BankRepository
    config=settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'data')
    assert config.question_bank_dir.resolve().is_relative_to(tmp_path.resolve())
    with BankRepository(config.question_bank_dir/'question_bank.sqlite3') as repo:
        repo.save_discovery([{'id':COURSE_ID,'name':'集合与图论','path':'public/resources/集合与图论'}],[])
        with repo.conn:
            sid=repo.save_source(Source(course_id=COURSE_ID,repository='synthetic/test',
                path='test.txt',url='https://example.org/test.txt'))
        repo.replace_parsed(sid,ParsedSource(pages=[Page(ordinal=1,text='1. 证明等价关系具有传递性。')],
            questions=[Question(number='1',text='证明等价关系具有传递性。',pages=[1],topics=['等价关系'],question_type='证明')],
            category='exam',year=2022,exam='final'))
        qid=repo.rows(COURSE_ID)[0]['id']
    remote_requests=[]
    def handle(request):
        remote_requests.append(request)
        payload=json.loads(request.content)
        user=json.loads(payload['messages'][-1]['content'])
        if 'previous_search' in user:
            result={'query':'等价关系证明题','year':2022,'exam':'final','question_type':'证明','limit':1,'clarification':''}
        else:result={'selected':[{'id':qid,'reason':'等价关系证明题符合筛选条件'}],'missing':''}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(result,ensure_ascii=False)}}]})
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    with closing(UIService(config,tmp_path/'ui.sqlite3')) as service:
        server=make_server(service,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with original(base_url=f'http://127.0.0.1:{server.server_port}') as client:
                def job(path,payload):
                    response=client.post(path,json=payload);assert response.status_code==200
                    key=response.json()['job_id']
                    deadline=time.monotonic()+10
                    while time.monotonic()<deadline:
                        value=client.get('/api/job',params={'id':key}).json()
                        if value['status']!='running':return value
                        time.sleep(.01)
                    raise AssertionError('HTTP job timed out')
                assert client.get('/models.js').status_code==200
                assert '具体模型型号' in client.get('/').text
                assert client.post('/api/model-settings',json=preferences(),headers={'Origin':'https://other.example'}).status_code==403
                saved=job('/api/model-settings',preferences())
                assert saved['status']=='complete'
                public=client.get('/api/model-settings').json()
                assert 'synthetic-llm-secret' not in json.dumps(public)
                session=client.post('/api/session',json={'scope':''}).json()['id']
                result=job('/api/chat',{'session':session,'question':'找集合与图论2022年期末等价关系证明题'})
                reply=result['result']['reply']
                assert reply['questions'][0]['id']==qid and reply['output_kind']=='question_selected'
                assert reply['questions'][0]['content_format']=='plain'
                assert reply['questions'][0]['text']=='证明等价关系具有传递性。'
                history=client.get('/api/history',params={'session':session}).json()
                assert history['turns'][0]['reply']['output_kind']=='question_selected'
                assert len(remote_requests)==2
                invalid=preferences();invalid['api']['temperature']='invalid'
                failed=job('/api/model-settings',invalid)
                assert failed['status']=='failed' and 'synthetic-llm-secret' not in json.dumps(failed)
        finally:
            server.shutdown();server.server_close();thread.join()
