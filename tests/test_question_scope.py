import threading
import time

import httpx
import pytest

from aitown.config import Settings
from aitown.question_bank.conversation import course_mention
from aitown.question_bank.models import COURSE_ID, Page, ParsedSource, Question, Source
from aitown.question_bank.models import SearchArgs
from aitown.question_bank.repository import BankRepository
from aitown.runtime.checkpoints import TurnPaused
from aitown.ui.demo import settings_for_ui
from aitown.ui.server import make_server
from aitown.ui.service import UIError, UIService


@pytest.fixture
def scoped_ui(tmp_path):
    config = settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'), tmp_path/'data')
    with BankRepository(config.question_bank_dir/'question_bank.sqlite3') as repo:
        repo.save_discovery([{'id': COURSE_ID, 'name': '集合与图论', 'path': 'synthetic/course'}], [])
        with repo.conn:
            sid = repo.save_source(Source(course_id=COURSE_ID, repository='synthetic/scope',
                path='scope.txt', url='https://example.org/scope.txt'))
        repo.replace_parsed(sid, ParsedSource(pages=[Page(ordinal=1, text='排列组合与欧拉图')],
            questions=[Question(number='1', text='用排列组合计算选取方案数。', pages=[1], topics=['排列组合'], question_type='计算'),
                       Question(number='2', text='证明欧拉图的度数性质。', pages=[1], topics=['欧拉图'], question_type='证明')],
            category='exam', year=2025, exam='midterm'))
    service = UIService(config, tmp_path/'ui.sqlite3')
    try:
        yield service
    finally:
        service.close()


def ask(service, session, query):
    return service.call('_chat', {'session': session, 'question': query})['reply']


def reopen(service):
    def restart():
        service.agent.close(); service.meta.close(); service._start()
    service.pool.submit(restart).result()


def test_mention_pins_followups_restart_and_session_isolation(scoped_ui):
    service = scoped_ui
    one = service.call('_create_session', {'scope': 'synthetic'})['id']
    two = service.call('_create_session', {'scope': 'synthetic'})['id']
    first = ask(service, one, '@集合与图论 排列组合的有哪些')
    assert first['debug']['decision']['calls'][0]['name'] == 'question_search'
    assert '@' not in first['debug']['decision']['calls'][0]['arguments']['query']
    assert first['questions'][0]['topics'] == ['排列组合']
    assert first['profile'] == [] and first['style'] == []
    assert service.call('_history', one)['session']['question_course'] == COURSE_ID
    assert service.call('_history', two)['session']['question_course'] == ''
    assert not ask(service, two, '排列组合的有哪些')['questions']
    reopen(service)
    assert service.call('_history', one)['session']['question_course'] == COURSE_ID
    follow = ask(service, one, '欧拉图的有哪些')
    assert follow['questions'][0]['topics'] == ['欧拉图']
    corrected = ask(service, one, '我说的是证明题')
    assert corrected['questions'][0]['question_type'] == '证明'
    service.call('_question_scope', {'session': one, 'course': ''})
    assert not ask(service, one, '排列组合的有哪些')['questions']
    assert '茶' in ask(service, one, '我喜欢咖啡还是茶？')['answer']


def test_natural_browse_words_do_not_dilute_topic_match(scoped_ui):
    def search():
        bank = scoped_ui.agent.question_bank
        plain = bank.search(SearchArgs(query='排列组合'))
        natural = bank.search(SearchArgs(query='排列组合的有哪些'))
        assert plain.question_ids and natural.question_ids == plain.question_ids
    scoped_ui.pool.submit(search).result()


def test_mention_only_and_personal_statement_do_not_call_remote_or_write_facts(scoped_ui):
    service = scoped_ui
    sid = service.call('_create_session', {'scope': 'synthetic'})['id']
    def prepare():
        before = service.agent.runtime_repository.conn.execute('SELECT count(*) FROM facts').fetchone()[0]
        service.agent.question_bank.config.similar_provider = 'api'
        return before
    before = service.pool.submit(prepare).result()
    result = ask(service, sid, '＠集合与图论')
    assert result['stop_reason'] == 'clarification' and '已固定' in result['answer']
    assert not result['debug']['decision']['calls']
    result = ask(service, sid, '记住：我不喜欢咖啡')
    assert result['stop_reason'] == 'clarification' and '取消固定' in result['answer']
    assert not result['debug']['decision']['calls'] and not result['debug']['model_calls']
    after = service.pool.submit(lambda:service.agent.runtime_repository.conn.execute('SELECT count(*) FROM facts').fetchone()[0]).result()
    assert before == after


def test_pinned_api_uses_plain_question_without_personal_history(scoped_ui, monkeypatch):
    from aitown.providers import ProviderConfig
    service = scoped_ui
    sid = service.call('_create_session', {'scope': 'synthetic'})['id']
    ask(service, sid, '我喜欢咖啡还是茶？')
    service.call('_question_scope', {'session': sid, 'course': COURSE_ID})
    requests = []
    def handle(request):
        import json
        payload = json.loads(request.content)
        requests.append(payload)
        text = json.dumps(payload, ensure_ascii=False)
        assert '咖啡' not in text and '喜欢茶' not in text and '@集合与图论' not in text
        user = json.loads(payload['messages'][-1]['content'])
        if 'previous_search' in user:
            result = {'query': '排列组合', 'year': None, 'exam': None, 'question_type': None, 'limit': 3, 'clarification': ''}
        else:
            candidate = next(q for q in user['candidates'] if '排列组合' in q['topics'])
            result = {'selected': [{'id': candidate['id'], 'reason': '排列组合计数题'}], 'missing': ''}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result, ensure_ascii=False)}}]})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs:original(transport=httpx.MockTransport(handle), **kwargs))
    def configure():
        service.agent.question_bank.config.similar_provider = 'api'
        service.agent.question_bank.config.api = ProviderConfig(model='synthetic-model', api_key='synthetic-key')
    service.pool.submit(configure).result()
    result = ask(service, sid, '排列组合的有哪些')
    assert result['output_kind'] == 'question_selected' and result['questions'][0]['topics'] == ['排列组合']
    assert len(requests) == 2


@pytest.mark.parametrize('query,expected', [('@集合与图论 排列组合', COURSE_ID), ('＠集合与图论，排列组合', COURSE_ID),
    ('@集合与图论', COURSE_ID), ('name@集合与图论', ''), ('@集合与图论笔记', ''), ('@线性代数', '')])
def test_only_registered_explicit_mentions_select_course(query, expected):
    assert course_mention(query) == expected


def test_pending_turn_recovers_original_course_even_without_checkpoint(scoped_ui):
    service = scoped_ui
    sid = service.call('_create_session', {})['id']
    service.call('_question_scope', {'session': sid, 'course': COURSE_ID})
    def pause():
        with pytest.raises(TurnPaused):
            service.agent.ask('排列组合的有哪些', sid, question_course=COURSE_ID, interrupt_after=['context'])
        assert service.agent.memory.pending(sid, service.settings.agent.id)['question_course'] == COURSE_ID
        service.agent.checkpoints.saver.delete_thread(service.agent.memory.thread_id(sid, service.settings.agent.id))
    service.pool.submit(pause).result()
    service.call('_question_scope', {'session': sid, 'course': ''})
    reopen(service)
    result = service.call('_resume', sid)['reply']
    assert result['questions'][0]['topics'] == ['排列组合']
    assert not service.call('_history', sid)['pending']


def test_question_scope_http_and_unknown_course_protection(scoped_ui):
    service = scoped_ui
    sid = service.call('_create_session', {})['id']
    with pytest.raises(UIError):
        service.call('_question_scope', {'session': sid, 'course': 'unknown-course'})
    with pytest.raises(UIError):
        service.call('_question_scope', {'session': 'missing', 'course': COURSE_ID})
    server = make_server(service, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as client:
            assert client.get('/question-scope.js').status_code == 200
            assert 'question-course-options' in client.get('/').text
            payload = {'session': sid, 'course': COURSE_ID}
            assert client.post('/api/question-scope', json=payload, headers={'Origin': 'https://other.example'}).status_code == 403
            assert client.post('/api/question-scope', json=payload).json()['question_course'] == COURSE_ID
            job = client.post('/api/chat', json={'session': sid, 'question': '排列组合的有哪些'}).json()['job_id']
            deadline = time.monotonic()+10
            while time.monotonic()<deadline:
                response = client.get('/api/job', params={'id': job}).json()
                if response['status'] != 'running':break
                time.sleep(.01)
            assert response['status'] == 'complete'
            assert response['result']['reply']['questions'][0]['topics'] == ['排列组合']
    finally:
        server.shutdown(); server.server_close(); worker.join()
