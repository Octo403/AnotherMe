import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from aitown.config import Settings
from aitown.errors import AgentError
from aitown.graph import AnotherMeAgent
from aitown.llm import CompatibleModel
from aitown.models import AnswerPayload, Citation, Claim, Decision, ReviewPayload, SkillCall, SkillResult
from aitown.nodes.checks import check
from aitown.nodes.common import call_model
from aitown.retrieval import SearchResult
from aitown.runtime import UserStatement
from aitown.style import compose_prompt
from aitown.style.models import ProfileClaim
from test_runtime import Counter


def agent_at(tmp_path):
    directory = tmp_path/'knowledge'
    directory.mkdir()
    (directory/'library.md').write_text('图书馆活动室容量7人。读书交流会每人费用15元。预约需提前一天。', encoding='utf-8')
    return AnotherMeAgent(Settings(knowledge_dir=directory, memory_db=tmp_path/'legacy.sqlite3',
        runtime_database=tmp_path/'app.sqlite3', checkpoint_db=tmp_path/'checkpoints.sqlite3',
        retrieval_database=tmp_path/'app.sqlite3', retrieval_index_dir=tmp_path/'retrieval'))


class Model:
    def __init__(self, decision=None, draft=None, review=None):
        self.responses = {Decision: decision or Decision(intent='knowledge', query='图书馆活动室容量'),
                          AnswerPayload: draft or AnswerPayload(answer='7人', source_refs=['library.md#1']),
                          ReviewPayload: review or ReviewPayload(answer='7人', source_refs=['library.md#1'])}
        self.calls = []

    def structured(self, messages, schema):
        self.calls.append(schema.__name__)
        result = self.responses[schema]
        return result(messages) if callable(result) else result.model_copy(deep=True)

    def close(self):
        pass


@pytest.mark.parametrize('repair_success', [True, False])
def test_json_repair_has_one_request_limit_and_no_secret_errors(repair_success):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        content = '{bad-json' if len(requests) == 1 or not repair_success else '{"answer":"你好"}'
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
    model = CompatibleModel(Settings(mode='live', model='synthetic', api_key='private-test-token',
                                    base_url='http://127.0.0.1', llm_response_format='json_schema'))
    model.client.close()
    model.client = httpx.Client(base_url='http://127.0.0.1/', transport=httpx.MockTransport(handler))
    try:
        if repair_success:
            assert model.structured([{'role': 'user', 'content': '你好'}], AnswerPayload).answer == '你好'
        else:
            with pytest.raises(AgentError) as caught:
                model.structured([{'role': 'user', 'content': '你好'}], AnswerPayload)
            assert caught.value.info.category == 'structured'
            assert 'private-test-token' not in caught.value.info.model_dump_json()
        assert len(requests) == len(model.events) == 2
        assert model.events[0].status == 'repair'
        assert requests[0]['response_format']['type'] == 'json_schema'
    finally:
        model.close()


def test_http_failure_is_typed_and_not_json_retried():
    count = []
    def handler(request):
        count.append(request)
        return httpx.Response(401, json={'error': 'private-test-token'})
    model = CompatibleModel(Settings(mode='live', model='synthetic', api_key='private-test-token', base_url='http://127.0.0.1'))
    model.client.close()
    model.client = httpx.Client(base_url='http://127.0.0.1/', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AgentError) as caught:
            model.structured([{'role': 'user', 'content': '你好'}], AnswerPayload)
        assert caught.value.info.category == 'model' and caught.value.info.status_code == 401
        assert len(count) == 1 and 'private-test-token' not in str(caught.value)
    finally:
        model.close()


def test_unknown_skill_is_recorded_but_never_executed(tmp_path):
    agent = agent_at(tmp_path)
    agent.model = Model(decision=Decision(intent='skill', query='活动室容量',
        calls=[SkillCall(name='skill', arguments={'name': 'chat'})]))
    try:
        reply = agent.ask('图书馆活动室容量多少')
        assert reply.skills == reply.tool_calls == []
        assert reply.decision.candidate_calls[0].name == 'skill'
        assert reply.errors[0].phase == 'intent'
        assert reply.answer == '7人' and reply.stop_reason == 'answered'
    finally:
        agent.close()


def test_person_reference_uses_user_sources_and_clarifies_two_candidates(tmp_path):
    agent = agent_at(tmp_path)
    try:
        first = agent.memory.save('s', 'anotherme', 'hx去过杭州', '模型猜测ql去了成都')
        single = agent.ask('他去过哪里', session='s', scope='synthetic')
        assert single.references[0].selected == 'hx' and single.references[0].turn_ids == [first]
        assert 'hx' in single.rewritten_question and single.decision.subject == 'other:hx'
        agent.memory.save('s', 'anotherme', 'ql去过成都', '随意输出')
        ambiguous = agent.ask('他去过哪里', session='s', scope='synthetic')
        assert ambiguous.stop_reason == 'clarification'
        assert set(ambiguous.references[0].candidates) == {'hx', 'ql'}
        assert ambiguous.sources == ambiguous.skills == []
        assert ambiguous.original_question == '他去过哪里'
    finally:
        agent.close()


def test_followup_does_not_reexecute_historical_calculation(tmp_path):
    agent = agent_at(tmp_path)
    try:
        agent.ask('15*2', session='s')
        reply = agent.ask('那件事是什么', session='s')
        assert reply.stop_reason == 'clarification' and reply.skills == reply.tool_calls == []
        other = agent.ask('他去过哪里', session='other', scope='synthetic')
        assert other.references[0].candidates == []
        turn = agent.memory.save('event', 'anotherme', '预约图书馆活动室', '助手猜测去过月球')
        for question in ('那件事需要提前预约吗', '之前说的需要提前预约吗'):
            reply = agent.ask(question, session='event')
            assert reply.references[0].selected == '预约图书馆活动室'
            assert turn in reply.references[0].turn_ids and reply.sources
            assert '图书馆活动室' in reply.rewritten_question and reply.skills == []
    finally:
        agent.close()


def test_explicit_self_lookup_retrieves_before_model_clarification(tmp_path):
    agent = agent_at(tmp_path)
    agent.model = Model(decision=Decision(intent='knowledge', query='我去过月球吗', needs_clarification=True,
        clarification_question='你指本人还是虚构人物？'))
    try:
        reply = agent.ask('我去过月球吗', scope='synthetic')
        assert not reply.decision.needs_clarification
        assert reply.stop_reason == 'no_evidence' and 'rag' in reply.trace
        assert agent.model.calls == ['Decision']
    finally:
        agent.close()


def test_claim_roles_keep_tool_facts_and_other_people_distinct():
    state = {'input': '计算6*7', 'sources': [], 'skills': [SkillResult(name='calculator', ok=True, data={'value': 42})],
             'decision': Decision(intent='skill', query='计算6*7', plan=['skill'])}
    valid = AnswerPayload(answer='42', claims=[Claim(text='42', subject='general', tool_refs=[0])])
    assert check(valid, state) == []
    bad = valid.model_copy(deep=True)
    bad.claims[0].source_refs = ['calculator:0']
    assert 'missing_reference' in {issue.code for issue in check(bad, state)}
    source = Citation(source='fact:1', chunk=0, text='hx去过杭州', subject='other:hx', score=1)
    state.update(input='hx去过杭州吗', sources=[source], skills=[], personal_query=True,
                 decision=Decision(intent='knowledge', query='hx去过杭州吗', plan=['rag']))
    valid = AnswerPayload(answer='hx去过杭州', source_refs=[source.ref],
        claims=[Claim(text='hx去过杭州', subject='other', subject_id='other:hx', source_refs=[source.ref])])
    assert check(valid, state) == []
    valid.claims[0].subject_id = 'other:ql'
    assert 'subject_mismatch' in {issue.code for issue in check(valid, state)}
    valid.claims[0].subject = 'self'
    assert 'subject_mismatch' in {issue.code for issue in check(valid, state)}


def test_personal_comparison_checks_each_object_without_combining_them():
    source = Citation(source='fact:2', chunk=0, text='不喜欢咖啡，喜欢茶', subject='self', score=1)
    state = {'input': '我喜欢咖啡还是茶', 'sources': [source], 'skills': [], 'personal_query': True,
             'decision': Decision(intent='knowledge', query='我喜欢咖啡还是茶', plan=['rag'])}
    payload = AnswerPayload(answer='我更喜欢茶而不是咖啡。', source_refs=[source.ref],
        claims=[Claim(text='我更喜欢茶而不是咖啡', subject='self', source_refs=[source.ref])])
    assert check(payload, state) == []
    payload.answer = payload.claims[0].text = '我喜欢茶和月球'
    assert 'unsupported_object' in {issue.code for issue in check(payload, state)}


def test_attributed_peer_quote_is_not_the_agents_own_experience():
    source=Citation(source='fact:7',chunk=0,text='hx说：我去过杭州，我没有去过南京。',subject='other:hx',score=1)
    state={'input':'hx没有去过南京，对吗？','sources':[source],'skills':[],'personal_query':True,
           'decision':Decision(intent='knowledge',query='hx没有去过南京，对吗？',plan=['rag'])}
    payload=AnswerPayload(answer='hx没有去过南京。根据资料，hx说：我去过杭州，我没有去过南京。',source_refs=[source.ref],
        claims=[Claim(text='hx没有去过南京',subject='other',subject_id='other:hx',source_refs=[source.ref])])
    assert check(payload,state)==[]
    payload.answer+='我去过杭州。'
    assert 'unsupported_personal_assertion' in {issue.code for issue in check(payload,state)}
    payload.answer='hx说：我去过月球。'
    assert 'unsupported_personal_assertion' in {issue.code for issue in check(payload,state)}
    payload.answer='ql说：我去过杭州，我没有去过南京。'
    assert 'unsupported_personal_assertion' in {issue.code for issue in check(payload,state)}


def test_mixed_preference_negates_the_object_instead_of_the_entire_source():
    source=Citation(source='fact:2',chunk=0,text='我不喜欢咖啡，喜欢茶',subject='self',score=1)
    state={'input':'我喜欢茶吗？','sources':[source],'skills':[],'personal_query':True,
           'decision':Decision(intent='knowledge',query='我喜欢茶吗？',plan=['rag'])}
    payload=AnswerPayload(answer='是的，我喜欢茶。',source_refs=[source.ref],
        claims=[Claim(text='我喜欢茶吗？',subject='self',source_refs=[source.ref])])
    assert check(payload,state)==[]
    payload.answer=payload.claims[0].text='我喜欢咖啡'
    assert 'negation_lost' in {issue.code for issue in check(payload,state)}
    payload.answer=payload.claims[0].text='我之前不喜欢咖啡，但现在我喜欢咖啡了。'
    assert 'negation_lost' in {issue.code for issue in check(payload,state)}


@pytest.mark.parametrize('question',['我哪天去过月球？','我拿过潜水证吗？','我之前当过飞行员吗？','我出生在哪家医院？','我参加过国际马拉松吗？','我现在喜欢咖啡吗？'])
def test_personal_paraphrases_without_sources_block_model_generation(tmp_path,question):
    agent=agent_at(tmp_path)
    model=Model(decision=Decision(intent='knowledge',query=question))
    agent.model=model
    try:
        result=agent.ask(question,scope='synthetic')
        assert result.stop_reason=='no_evidence'
        assert model.calls==['Decision']
        assert result.decision.subject=='self'
    finally:agent.close()


def test_unknown_self_subject_ignores_empty_model_skill_clarification(tmp_path):
    agent=agent_at(tmp_path)
    agent.model=Model(decision=Decision(intent='knowledge',query='我哪天去过月球？',plan=['skill'],
        needs_clarification=True,clarification_question='本人还是其他人？'))
    try:
        result=agent.ask('我哪天去过月球？',scope='synthetic')
        assert result.stop_reason=='no_evidence' and result.decision.plan==['rag']
        assert not result.decision.needs_clarification
    finally:agent.close()


def test_document_question_cannot_execute_copied_memory_tool_example(tmp_path):
    agent=agent_at(tmp_path)
    agent.model=Model(decision=Decision(intent='skill',query='活动室容量',calls=[SkillCall(name='memory_query',arguments={'topic':'复习'})]))
    try:
        result=agent.ask('活动室容量多少？',scope='synthetic')
        assert result.stop_reason=='answered' and result.skills==[]
        assert result.decision.plan==['rag']
        assert result.decision.candidate_calls[0].name=='memory_query'
        assert any(error.phase=='intent' for error in result.errors)
    finally:agent.close()


def test_as_of_iso_date_is_not_a_subtraction_request(tmp_path):
    agent=agent_at(tmp_path)
    agent.model=Model(decision=Decision(intent='knowledge',query='截至2025-09-01，我当时喜欢喝什么？'))
    try:
        result=agent.ask('截至2025-09-01，我当时喜欢喝什么？',scope='synthetic')
        assert result.skills==[] and result.decision.plan==['rag']
        assert result.decision.as_of is not None and result.decision.subject=='self'
        assert result.stop_reason=='no_evidence'
    finally:agent.close()


def test_negative_interest_idiom_cannot_become_affirmed_by_wrong_question():
    source=Citation(source='memory:1',chunk=0,text='我个人对计算机代码类也不是很感冒',subject='self',score=1)
    state={'input':'我对代码一直很感兴趣吗？','sources':[source],'skills':[],'personal_query':True,
           'decision':Decision(intent='knowledge',query='我对代码一直很感兴趣吗？',plan=['rag'])}
    answer=AnswerPayload(answer='我对计算机写代码一直很感兴趣。',source_refs=[source.ref],
        claims=[Claim(text='我对计算机写代码一直很感兴趣。',subject='self',source_refs=[source.ref])])
    for text in ['我对计算机代码类不是特别感兴趣。','我对计算机代码类不太感兴趣。','我对计算机代码类没有兴趣。']:
        negative=AnswerPayload(answer=text,source_refs=[source.ref],claims=[Claim(text=text,subject='self',source_refs=[source.ref])])
        assert 'negation_lost' not in {issue.code for issue in check(negative,state)}
    assert 'negation_lost' in {issue.code for issue in check(answer,state)}


def test_date_cutoff_is_grounded_but_wrong_user_price_is_not():
    point=int(datetime(2025,9,1,23,59,59,tzinfo=timezone(timedelta(hours=8))).timestamp())
    source=Citation(source='fact:1',chunk=0,text='喜欢咖啡',subject='self',score=1,
                    timestamp=point-40*86400)
    state={'input':'截至2025-09-01，我喜欢喝什么？','sources':[source],'skills':[],'personal_query':True,
           'decision':Decision(intent='knowledge',query='截止偏好',plan=['rag'],as_of=point)}
    good=AnswerPayload(answer='截至2025年9月1日，我喜欢咖啡。',source_refs=[source.ref],
        claims=[Claim(text='截至2025年9月1日，我喜欢咖啡。',subject='self',source_refs=[source.ref])])
    assert check(good,state)==[]
    bad=good.model_copy(deep=True);bad.claims[0].text='我花费999元'
    state['input']+='是不是花费999元'
    assert 'unsupported_number' in {issue.code for issue in check(bad,state)}


def test_clock_conversion_keeps_original_hour_and_cannot_justify_wrong_time():
    source=Citation(source='document:1',chunk=0,text='09:00至17:00开放',score=1)
    state={'input':'几点关门？','sources':[source],'skills':[],'decision':Decision(intent='knowledge',query='闭馆时间')}
    answer=AnswerPayload(answer='下午5点关门',source_refs=[source.ref],claims=[Claim(text='下午5点关门',source_refs=[source.ref])])
    assert check(answer,state)==[]
    answer.answer=answer.claims[0].text='下午4点关门'
    assert 'unsupported_number' in {issue.code for issue in check(answer,state)}


def test_named_peer_is_not_asked_who_they_are_after_model_clarification(tmp_path):
    agent=agent_at(tmp_path)
    agent.model=Model(decision=Decision(intent='knowledge',query='hx去过杭州吗？',needs_clarification=True,clarification_question='hx是谁？'))
    try:
        result=agent.ask('hx去过杭州吗？',scope='synthetic')
        assert result.stop_reason=='no_evidence' and result.decision.subject=='other:hx'
        assert not result.decision.needs_clarification
    finally:agent.close()


def test_provider_schema_uses_only_current_source_ids():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"answer":"你好"}'}}]})
    model = CompatibleModel(Settings(mode='live', model='synthetic', api_key='private-test-token',
        base_url='http://127.0.0.1', llm_response_format='json_schema'))
    model.client.close()
    model.client = httpx.Client(base_url='http://127.0.0.1/', transport=httpx.MockTransport(handler))
    source = Citation(source='fact:3', chunk=0, text='hx去过杭州', subject='other:hx', score=1)
    try:
        model.structured_evidence([{'role': 'user', 'content': '你好'}], AnswerPayload, [source])
        spec = requests[-1]['response_format']['json_schema']['schema']
        assert spec['properties']['source_refs']['items']['enum'] == [source.ref]
        claim = spec['$defs']['Claim']['properties']
        assert claim['subject_id']['enum'] == [None, 'other:hx']
        assert claim['source_refs']['items']['enum'] == [source.ref]
        model.structured_evidence([{'role': 'user', 'content': '你好'}], ReviewPayload, [])
        spec = requests[-1]['response_format']['json_schema']['schema']
        assert spec['properties']['source_refs']['maxItems'] == spec['$defs']['Claim']['properties']['source_refs']['maxItems'] == 0
    finally:
        model.close()


def test_style_provenance_does_not_exhaust_generation_context():
    claim = ProfileClaim(key='short', text='使用短句', scope='synthetic', basis='observed',
                         trait_id=1, source_ids=list(range(1, 1001)))
    prompt = compose_prompt('AnotherMe', claims=[claim])
    assert len(prompt) < 1000 and '使用短句' in prompt
    assert claim.source_ids == list(range(1, 1001))


def test_temporal_filters_are_derived_from_current_input(tmp_path, monkeypatch):
    now = datetime(2026, 10, 5, 12, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr('time.time', lambda: now.timestamp())
    agent = agent_at(tmp_path)
    captured = []
    class Search:
        def search(self, query, filters):
            captured.append(filters)
            return SearchResult(status='no_personal_data')
        def close(self):
            pass
    agent.retriever.close()
    agent.retriever = Search()
    try:
        reply = agent.ask('上个月我提过哪些计划', scope='synthetic')
        assert captured[0].time_from == int(datetime(2026, 9, 1, tzinfo=now.tzinfo).timestamp())
        assert captured[0].time_to == int(datetime(2026, 10, 1, tzinfo=now.tzinfo).timestamp())-1
        assert reply.decision.purpose == 'personal' and reply.stop_reason == 'no_evidence'
    finally:
        agent.close()


@pytest.mark.parametrize('question,plan,value', [
    ('查询读书交流会每人费用，并计算12人总费用', ['rag', 'skill'], 180),
    ('查询读书交流会每人费用，并计算12个人总费用', ['rag', 'skill'], 180),
    ('先计算6*7，再查询图书馆活动室容量', ['skill', 'rag'], 42),
])
def test_both_combination_orders_have_actual_results(tmp_path, question, plan, value):
    agent = agent_at(tmp_path)
    try:
        reply = agent.ask(question)
        assert reply.decision.plan == plan and reply.skills[0].data['value'] == value
        assert reply.sources and str(value) in reply.answer
        assert reply.trace == ['context', 'intent', *plan, 'reflect']
        assert len(agent.memory.rows('default', 'anotherme', recent=False)) == 1
    finally:
        agent.close()


def test_current_invitation_overrides_model_knowledge_but_past_experience_stays_personal(tmp_path):
    agent = agent_at(tmp_path)
    agent.model = Model(decision=Decision(intent='knowledge', query='下课一起去便利店买杯喝的？', plan=['rag']),
                        draft=AnswerPayload(answer='好的！'), review=ReviewPayload(answer='好的！'))
    try:
        reply = agent.ask('下课一起去便利店买杯喝的？')
        assert reply.decision.intent == 'chat' and reply.answer == '好的！'
        assert 'rag' not in reply.trace and not reply.sources
        unknown = agent.ask('去年我和朋友一起去便利店了吗？', scope='synthetic')
        assert unknown.decision.purpose == 'personal' and unknown.stop_reason == 'no_evidence'
        assert 'rag' in unknown.trace
        question='一起去图书馆活动室吗，容量是多少？'
        agent.model = Model(decision=Decision(intent='knowledge', query=question, plan=['rag']))
        mixed = agent.ask(question)
        assert mixed.decision.intent == 'knowledge' and 'rag' in mixed.trace
        assert not mixed.skills  # A mixed invitation/fact question still performs retrieval.
    finally:
        agent.close()


def test_failed_retrieval_does_not_invent_dependent_tool_arguments(tmp_path):
    agent = agent_at(tmp_path)
    try:
        (agent.settings.knowledge_dir/'library.md').unlink()
        reply = agent.ask('查询未知活动每人费用，并计算12人总费用')
        assert reply.skills == reply.tool_calls == []
        assert any(step.node == 'skill' and step.status == 'skipped' for step in reply.execution)
    finally:
        agent.close()


def test_reference_and_number_errors_are_corrected_and_inspectable(tmp_path):
    agent = agent_at(tmp_path)
    bad = AnswerPayload(answer='容量20人', source_refs=['invented#99'])
    good = ReviewPayload(answer='容量7人', source_refs=['library.md#1'], corrections=['移除不存在的引用并修正容量'],
                         claims=[Claim(text='容量7人', source_refs=['library.md#1'])])
    agent.model = Model(draft=bad, review=good)
    try:
        reply = agent.ask('图书馆活动室容量多少')
        assert reply.draft_answer == '容量20人' and reply.answer == '容量7人'
        codes = {issue.code for issue in reply.reviews[0].deterministic_issues}
        assert {'missing_reference', 'unsupported_number'} <= codes
        assert reply.reviews[0].final_issues == [] and reply.correction_count == 1
        assert reply.answer_source_refs == ['library.md#1']
    finally:
        agent.close()


def test_wrong_personal_object_cannot_pass_with_only_correction_notes(tmp_path):
    agent = agent_at(tmp_path)
    fact = agent.memory.record_statement(UserStatement(attribute='preference', value='喜欢茶', scope='synthetic'),
                                         '我喜欢茶', 's', 'anotherme')
    ref = f'fact:{fact}#0'
    bad = AnswerPayload(answer='我去过月球', source_refs=[ref], claims=[Claim(text='我去过月球', subject='self', source_refs=[ref])])
    agent.model = Model(decision=Decision(intent='knowledge', query='我喜欢茶吗'), draft=bad,
        review=ReviewPayload(**bad.model_dump(), corrections=['已修正无依据经历']))
    try:
        reply = agent.ask('我喜欢茶吗', session='s', scope='synthetic')
        assert reply.stop_reason == 'validation_failed' and '月球' not in reply.answer
        assert any(issue.code == 'unsupported_object' for issue in reply.reviews[0].final_issues)
        assert agent.runtime_repository.query_facts('月球') == []
    finally:
        agent.close()


def test_failed_tool_is_not_success_even_when_reviewer_agrees(tmp_path):
    agent = agent_at(tmp_path)
    bad = AnswerPayload(answer='已执行成功，结果99', claims=[Claim(text='结果99', tool_refs=[0])])
    agent.model = Model(draft=bad, review=ReviewPayload(**bad.model_dump()))
    try:
        reply = agent.ask('6/0')
        assert not reply.skills[0].ok and '99' not in reply.answer and '失败' in reply.answer
        assert reply.stop_reason == 'validation_failed'
        assert {'invalid_tool_reference', 'failed_tool_as_success'} <= {i.code for i in reply.reviews[0].final_issues}
    finally:
        agent.close()


def counter_model():
    return Model(decision=Decision(intent='skill', query='写入计数器', plan=['skill'], calls=[SkillCall(name='counter')]),
                 draft=AnswerPayload(answer='计数器值为1'), review=ReviewPayload(answer='计数器值为1'))


def counter_table(agent):
    with agent.memory.conn:
        agent.memory.conn.execute('CREATE TABLE test_counter(value INTEGER)')
        agent.memory.conn.execute('INSERT INTO test_counter VALUES(0)')
    agent.skills.register(Counter())


def test_supplement_is_bounded_and_never_repeats_write(tmp_path):
    agent = agent_at(tmp_path)
    counter_table(agent)
    model = counter_model()
    model.responses[ReviewPayload] = ReviewPayload(answer='计数器值为1', needs_retrieval=True,
                                                   retrieval_query='计数器说明', evidence_sufficient=False)
    agent.model = model
    try:
        reply = agent.ask('计数器写入一次，并查资料说明')
        assert reply.supplemental_count == 1 and len(reply.reviews) == 2
        assert reply.trace == ['context', 'intent', 'skill', 'reflect', 'rag', 'reflect']
        assert reply.stop_reason == 'evidence_insufficient' and len(reply.model_calls) == 5
        assert agent.memory.conn.execute('SELECT value FROM test_counter').fetchone()[0] == 1
        assert agent.memory.conn.execute('SELECT count(*) FROM runtime_skill_calls').fetchone()[0] == 1
        assert len(agent.memory.rows('default', 'anotherme', recent=False)) == 1
    finally:
        agent.close()


def test_database_failure_after_write_can_resume_without_duplicate(tmp_path, monkeypatch):
    agent = agent_at(tmp_path)
    cfg = agent.settings
    counter_table(agent)
    agent.model = counter_model()
    def failure(*args):
        raise sqlite3.OperationalError('private-test-token')
    monkeypatch.setattr(agent.memory, 'complete', failure)
    with pytest.raises(AgentError) as caught:
        agent.ask('计数器写入一次', session='s')
    assert caught.value.info.category == 'database' and 'private-test-token' not in str(caught.value)
    agent.close()
    agent = AnotherMeAgent(cfg)
    try:
        reply = agent.resume('s')
        assert reply.skills[0].ok
        assert agent.memory.conn.execute('SELECT value FROM test_counter').fetchone()[0] == 1
        assert len(agent.memory.rows('s', 'anotherme', recent=False)) == 1
    finally:
        agent.close()


def test_checkpoint_failure_after_write_can_resume_without_duplicate(tmp_path, monkeypatch):
    agent = agent_at(tmp_path)
    cfg = agent.settings
    counter_table(agent)
    agent.model = counter_model()
    original = agent.checkpoints.saver.put
    def failure(config, checkpoint, metadata, versions):
        if checkpoint.get('channel_values', {}).get('skills'):
            raise sqlite3.OperationalError('synthetic checkpoint write failure')
        return original(config, checkpoint, metadata, versions)
    monkeypatch.setattr(agent.checkpoints.saver, 'put', failure)
    with pytest.raises(AgentError) as caught:
        agent.ask('计数器写入一次', session='s')
    assert caught.value.info.category == 'checkpoint'
    agent.close()
    agent = AnotherMeAgent(cfg)
    agent.skills.register(Counter())
    try:
        agent.resume('s')
        assert agent.memory.conn.execute('SELECT value FROM test_counter').fetchone()[0] == 1
        assert agent.memory.conn.execute('SELECT count(*) FROM runtime_skill_calls').fetchone()[0] == 1
    finally:
        agent.close()


def test_changed_sources_in_generation_are_refreshed_without_stale_drafts(tmp_path):
    agent = agent_at(tmp_path)
    file = agent.settings.knowledge_dir/'library.md'
    calls = []
    def draft(messages):
        calls.append(1)
        if len(calls) == 1:
            file.write_text('图书馆活动室已关闭。', encoding='utf-8')
            return AnswerPayload(answer='活动室容量7人', source_refs=['library.md#1'])
        return AnswerPayload(answer='活动室已关闭', source_refs=['library.md#1'])
    def review(messages):
        payload = json.loads(messages[-1]['content'])['draft']
        return ReviewPayload(**payload)
    agent.model = Model(draft=draft, review=review)
    try:
        reply = agent.ask('图书馆活动室容量多少')
        assert reply.supplemental_count == 1 and reply.answer == '活动室已关闭'
        assert '容量7人' not in reply.model_dump_json()
        assert '每人费用15元' not in reply.model_dump_json()
    finally:
        agent.close()


def test_model_fault_clears_on_next_turn(tmp_path):
    agent = agent_at(tmp_path)
    attempts = []
    def decide(messages):
        attempts.append(1)
        if len(attempts) == 1:
            raise AgentError('structured', 'intent', 'json_validation', '合成坏 JSON')
        return Decision(intent='chat', query='你好')
    agent.model = Model(decision=decide, draft=AnswerPayload(answer='你好'), review=ReviewPayload(answer='你好'))
    try:
        assert agent.ask('你好').errors[0].category == 'structured'
        following = agent.ask('你好')
        assert following.errors == following.warnings == [] and following.correction_count == 0
    finally:
        agent.close()


def test_prompt_budget_preserves_current_request_and_evidence(tmp_path):
    agent = agent_at(tmp_path)
    agent.settings.context_max_chars = 2000
    captured = []
    agent.model = Model(draft=lambda messages: captured.extend(messages) or AnswerPayload(answer='ok'))
    state = {'warnings': [], 'model_calls': []}
    messages = [{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': 'old'*500},
                {'role': 'assistant', 'content': 'guess'*400}, {'role': 'user', 'content': 'evidence'},
                {'role': 'user', 'content': 'current request'}]
    try:
        call_model(agent, state, messages, AnswerPayload, 'draft')
        assert captured == [messages[0], messages[-2], messages[-1]]
        assert state['warnings'] and len(state['model_calls']) == 1
    finally:
        agent.close()
