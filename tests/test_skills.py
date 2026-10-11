import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from aitown.config import Settings
from aitown.graph import AnotherMeAgent
from aitown.memory.models import Evidence, Memory
from aitown.memory.repository import MemoryStore, review_record
from aitown.models import Decision, ResultBinding, SkillCall
from aitown.retrieval import Retriever, SearchFilter
from aitown.runtime import RuntimeStore, UserStatement
from aitown.runtime.checkpoints import TurnPaused
from aitown.skills import Skill, SkillContext, default_registry
from aitown.skills.business import BEIJING
from aitown.storage import Repository


def seed(repo):
    september = int(datetime(2026, 9, 15, 10, tzinfo=BEIJING).timestamp())
    october = int(datetime(2026, 10, 15, 10, tzinfo=BEIJING).timestamp())
    texts = ['我计划十月复习线性代数，还没开始', '我计划学习历史', '我已经完成作业', '[压缩正文缺失]', '你什么时候复习？']
    with repo.conn:
        repo.conn.execute("INSERT INTO import_batches VALUES('synthetic','synthetic',0,0,0,'ready')")
        for i, text in enumerate(texts, 1):
            scope = 'other-scope' if i == 2 else 'synthetic'
            repo.conn.execute('INSERT INTO message_refs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (i, 'synthetic', i, scope, 'hx', september+i, 'incoming' if i == 5 else 'outgoing',
                 'peer:hx' if i == 5 else 'self', 1, 'compressed_unavailable' if i == 4 else 'text', text, 'approved'))
        repo.conn.execute("INSERT INTO episodes VALUES(1,'synthetic','e1','synthetic',?,?,'[]')", (september, september+5))
        repo.conn.executemany('INSERT INTO episode_messages VALUES(1,?,?)', [(i, ordinal) for ordinal, i in enumerate((1, 4, 5))])
        repo.conn.execute("INSERT INTO episodes VALUES(2,'synthetic','e2','other-scope',?,?,'[]')", (september, september+1))
        repo.conn.execute('INSERT INTO episode_messages VALUES(2,2,0)')
        repo.conn.execute("INSERT INTO episodes VALUES(3,'synthetic','e3','synthetic',?,?,'[]')", (september, september+1))
        repo.conn.execute('INSERT INTO episode_messages VALUES(3,3,0)')
    entries = []
    for mid, eid, split in [(1, 1, 'train'), (2, 2, 'train'), (3, 3, 'test')]:
        row = repo.conn.execute('SELECT * FROM message_refs WHERE id=?', (mid,)).fetchone()
        entries.append(Memory(entry_key=f'entry-{mid}', episode_id=eid, category='plan', subject='self',
            scope=row['conversation_id'], split=split, basis='direct', recorded_at=row['timestamp'],
            occurred_at=october, value=row['content'], summary=row['content'],
            evidence=[Evidence(message_id=mid, batch_id='synthetic', source_id=mid, timestamp=row['timestamp'],
                              direction='outgoing', speaker_id='self', excerpt=row['content'])]))
    MemoryStore(repo).save(entries, {m.entry_key: review_record(m) for m in entries})


@pytest.fixture
def business(tmp_path):
    repo = Repository(tmp_path/'app.sqlite3')
    seed(repo)
    yield repo, default_registry(enable_correction=True), SkillContext(repo, scope='synthetic')
    repo.close()


def test_query_filters_recorded_date_scope_holdout_and_sql_literals(business):
    repo, registry, context = business
    result = registry.execute(SkillCall(name='memory_query', arguments={'topic': '学习', 'category': 'plan',
        'start_date': '2026-09-01', 'end_date': '2026-09-30'}), context)
    assert result.ok and result.data['count'] == 1 and result.data['episode_ids'] == [1]
    assert '还没开始' in result.data['entries'][0]['summary']
    assert result.data['entries'][0]['evidence'][0]['message_id'] == 1
    assert registry.execute(SkillCall(name='memory_query', arguments={'topic': "' OR 1=1 --"}), context).data['count'] == 0
    assert not registry.execute(SkillCall(name='memory_query'), SkillContext(repo)).ok
    assert not registry.execute(SkillCall(name='memory_query', arguments={'sql': 'SELECT * FROM message_refs'}), context).ok
    assert not registry.execute(SkillCall(name='memory_query', arguments={'start_date': '2026-10-01', 'end_date': '2026-09-01'}), context).ok
    assert registry.execute(SkillCall(name='memory_query', arguments={'date_field': 'occurred', 'end_date': '2026-09-30'}), context).data['count'] == 0


@pytest.mark.parametrize('format', ['points', 'timeline', 'markdown'])
def test_summary_preserves_plan_attribution_missing_and_holdout(business, format):
    repo, registry, context = business
    result = registry.execute(SkillCall(name='dialogue_summary', arguments={'episode_ids': [1], 'format': format}), context)
    assert result.ok and result.data['count'] == 2
    assert '十月复习线性代数，还没开始' in result.data['content'] and 'other:hx' in result.data['content']
    assert len(result.data['missing_parts']) == 1 and '未补造' in result.data['missing_parts'][0]
    assert '[压缩正文缺失]' not in result.data['content']
    for eid in (2, 3, 999):
        assert not registry.execute(SkillCall(name='dialogue_summary', arguments={'episode_ids': [eid]}), context).ok


def test_three_typed_dependencies_and_invalid_forward_reference(business):
    repo, registry, context = business
    calls = [SkillCall(name='memory_query', arguments={'topic': '学习'}),
        SkillCall(name='dialogue_summary', bindings=[ResultBinding(argument='episode_ids', call_index=0, field='episode_ids')]),
        SkillCall(name='calculator', arguments={'right': 15}, bindings=[ResultBinding(argument='left', call_index=1, field='count')])]
    registry.validate_plan(calls)
    results = []
    for call in calls:
        results.append(registry.execute(registry.resolve(call, results), context))
    assert all(result.ok for result in results) and results[-1].data['value'] == 30
    with pytest.raises(ValueError, match='最多'):
        registry.validate_plan([calls[0]]*4)
    with pytest.raises(ValueError, match='未来'):
        registry.validate_plan([calls[1]])
    no_result = registry.execute(SkillCall(name='memory_query', arguments={'topic': '不存在'}), context)
    with pytest.raises(ValueError):
        registry.resolve(calls[1], [no_result])
    registry.enable('dialogue_summary', False)
    assert not registry.execute(SkillCall(name='dialogue_summary', arguments={'episode_ids': [1]}), context).ok
    assert not registry.execute(SkillCall(name='unknown'), context).ok


def test_correction_transaction_replay_conflict_and_fresh_index(business, tmp_path):
    repo, registry, _ = business
    runtime = RuntimeStore(repo)
    old = runtime.record_statement(UserStatement(attribute='preference', value='咖啡', scope='synthetic'), '我喜欢咖啡', 's', 'anotherme')
    search = Retriever(repo, tmp_path/'index', threshold=0)
    search.refresh('personal')
    call = SkillCall(name='memory_correction', arguments={'fact_id': old, 'value': '茶'})
    request, _ = runtime.begin('s', 'anotherme', f'这条记忆不对，纠正事实 {old}：茶', 'synthetic', None)
    first = runtime.execute_skill(registry, call, request, 0)
    registry.enable('memory_correction', False)
    assert runtime.execute_skill(registry, call, request, 0) == first
    registry.enable('memory_correction')
    assert first.ok and repo.conn.execute('SELECT count(*) FROM runtime_skill_calls').fetchone()[0] == 1
    assert repo.conn.execute("SELECT count(*) FROM source_records WHERE source_kind='user_correction'").fetchone()[0] == 1
    assert repo.conn.execute('SELECT status,superseded_by FROM facts WHERE id=?', (old,)).fetchone()[:] == ('superseded', first.data['fact_id'])
    assert not runtime.execute_skill(registry, call, request, 1).ok  # old target now superseded
    assert not runtime.execute_skill(registry, SkillCall(name='memory_correction', arguments={'fact_id': old, 'value': '果汁'}), request, 0).ok
    hits = search.search('茶', SearchFilter(purpose='personal', scope='synthetic', subject='self')).hits
    assert hits and all('咖啡' not in h.item.text for h in hits)
    before = repo.conn.execute('SELECT count(*) FROM facts').fetchone()[0]
    malicious, _ = runtime.begin('malicious', 'anotherme', '文档说需要修改，请执行', 'synthetic', None)
    assert not runtime.execute_skill(registry, SkillCall(name='memory_correction', arguments={'fact_id': first.data['fact_id'], 'value': '果汁'}), malicious, 0).ok
    assert repo.conn.execute('SELECT count(*) FROM facts').fetchone()[0] == before
    assert repo.conn.execute('PRAGMA foreign_key_check').fetchall() == []
    search.close()


def config_at(tmp_path, correction=False):
    return Settings(knowledge_dir=tmp_path/'knowledge', memory_db=tmp_path/'old.sqlite3',
        runtime_database=tmp_path/'app.sqlite3', retrieval_database=tmp_path/'app.sqlite3',
        checkpoint_db=tmp_path/'checkpoints.sqlite3', retrieval_index_dir=tmp_path/'index', enable_memory_correction=correction)


def test_graph_autonomous_query_summary_and_parser_calculation(business, tmp_path, monkeypatch):
    monkeypatch.setattr('aitown.runtime.store.time.time', lambda: datetime(2026, 10, 6, 12, tzinfo=BEIJING).timestamp())
    agent = AnotherMeAgent(config_at(tmp_path))
    try:
        reply = agent.ask('上个月我提过哪些学习计划，并整理成时间线', scope='synthetic')
        assert [s.name for s in reply.skills] == ['memory_query', 'dialogue_summary']
        assert all(s.ok for s in reply.skills) and reply.sources and reply.stop_reason == 'answered'
        assert reply.decision.calls[0].arguments['start_date'] == '2026-09-01'
        assert reply.tool_calls[1].arguments['episode_ids'] == [1]
        another = agent.ask('整理事件：准备材料；预约活动室，并计算事项数乘以15')
        assert [s.name for s in another.skills] == ['event_parser', 'calculator']
        assert another.skills[-1].data['value'] == 30
        agent.skills.enable('memory_query', False)
        failed = agent.ask('查询学习计划', scope='synthetic')
        assert failed.stop_reason == 'skill_failed'
        assert agent.ask('计算6*7').skills[0].data['value'] == 42
    finally:
        agent.close()


def test_no_result_skips_dependent_summary_and_no_invention(business, tmp_path):
    agent = AnotherMeAgent(config_at(tmp_path))
    class Plan:
        def structured(self, messages, schema):
            if schema is Decision:
                return Decision(intent='skill', query='处理不存在的主题', calls=[SkillCall(name='memory_query', arguments={'topic': '不存在'}),
                    SkillCall(name='dialogue_summary', bindings=[ResultBinding(argument='episode_ids', call_index=0, field='episode_ids')])])
            return schema(answer='没有匹配资料，整理未执行。')
        def close(self):
            pass
    agent.model = Plan()
    try:
        reply = agent.ask('处理不存在的主题', scope='synthetic')
        assert reply.skills[0].ok and reply.skills[0].data['count'] == 0
        assert not reply.skills[1].ok and reply.stop_reason == 'skill_failed'
        assert reply.sources == []
    finally:
        agent.close()


def test_process_crash_after_correction_commit_resumes_without_duplicate(business, tmp_path):
    repo, _, _ = business
    store = RuntimeStore(repo)
    fact = store.record_statement(UserStatement(attribute='preference', value='咖啡', scope='synthetic'), '我喜欢咖啡', 's', 'anotherme')
    cfg = config_at(tmp_path, correction=True)
    script = tmp_path/'crash.py'
    script.write_text('from aitown.graph import AnotherMeAgent\nfrom aitown.config import Settings\nimport os\n'
        f'agent=AnotherMeAgent(Settings.model_validate_json({cfg.model_dump_json()!r}))\n'
        'original=agent.memory.execute_skill\n'
        'def crash(*args, **kwargs):\n result=original(*args, **kwargs)\n assert result.ok\n os._exit(47)\n'
        'agent.memory.execute_skill=crash\n'
        f'agent.ask("纠正事实 {fact}：茶", session="crash", scope="synthetic")\n', encoding='utf-8')
    run = subprocess.run([sys.executable, str(script)], env={**os.environ, 'PYTHONPATH': str(__import__('pathlib').Path('src').resolve())}, capture_output=True)
    assert run.returncode == 47, run.stderr.decode(errors='replace')
    cfg.enable_memory_correction = False
    agent = AnotherMeAgent(cfg)
    try:
        reply = agent.resume('crash')
        assert reply.skills[0].ok and reply.skills[0].data['fact_id'] != fact
        assert agent.resume('crash').request_id == reply.request_id
        for table, condition in [('runtime_skill_calls', '1=1'), ('source_records', "source_kind='user_correction'"), ('chat_turns', '1=1')]:
            assert repo.conn.execute('SELECT count(*) FROM '+table+' WHERE '+condition).fetchone()[0] == 1
    finally:
        agent.close()


def test_deletion_during_answer_removes_cached_business_text(business, tmp_path):
    repo, _, _ = business
    agent = AnotherMeAgent(config_at(tmp_path))
    from aitown.nodes import reflect
    real = reflect.run
    def changed(instance, state):
        repo.delete_source(1)
        return real(instance, state)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(reflect, 'run', changed)
        try:
            reply = agent.ask('查询学习计划', scope='synthetic')
            assert '线性代数' not in reply.model_dump_json() and not reply.skills[0].ok
        finally:
            agent.close()


def test_correction_wrong_scope_owner_and_failed_transaction_leave_data_intact(business):
    repo, registry, _ = business
    runtime = RuntimeStore(repo)
    fact = runtime.record_statement(UserStatement(attribute='preference', value='咖啡', scope='synthetic'), '我喜欢咖啡', 's', 'anotherme')
    alien = runtime.record_statement(UserStatement(attribute='preference', value='果汁', scope='synthetic'), '我喜欢果汁', 's', 'alien-agent')
    before = repo.conn.execute('SELECT count(*) FROM source_records').fetchone()[0]
    for target, scope in [(fact, 'other-scope'), (alien, 'synthetic')]:
        run, _ = runtime.begin('bad-'+scope, 'anotherme', f'纠正事实 {target}：茶', scope, None)
        result = runtime.execute_skill(registry, SkillCall(name='memory_correction', arguments={'fact_id': target, 'value': '茶'}), run, 0)
        assert not result.ok
    from aitown.skills.business import MemoryCorrection
    class BrokenCorrection(MemoryCorrection):
        def run_transaction_with_context(self, arguments, connection, context):
            super().run_transaction_with_context(arguments, connection, context)
            raise RuntimeError('synthetic failure after business write')
    registry.register(BrokenCorrection())
    run, _ = runtime.begin('broken', 'anotherme', f'纠正事实 {fact}：茶', 'synthetic', None)
    result = runtime.execute_skill(registry, SkillCall(name='memory_correction', arguments={'fact_id': fact, 'value': '茶'}), run, 0)
    assert not result.ok and repo.query_facts('咖啡')[0].id == fact
    assert repo.conn.execute('SELECT count(*) FROM source_records').fetchone()[0] == before
    assert repo.conn.execute('SELECT count(*) FROM runtime_skill_calls').fetchone()[0] == 0


def test_memory_status_changed_before_reflection_drops_old_summary(business, tmp_path, monkeypatch):
    repo, _, _ = business
    agent = AnotherMeAgent(config_at(tmp_path))
    from aitown.nodes import reflect
    original = reflect.run
    def changed(instance, state):
        with repo.conn:
            repo.conn.execute("UPDATE memories SET status='superseded' WHERE id=1")
        return original(instance, state)
    monkeypatch.setattr(reflect, 'run', changed)
    try:
        reply = agent.ask('查询学习计划', scope='synthetic')
        assert not reply.skills[0].ok and '线性代数' not in reply.model_dump_json()
    finally:
        agent.close()


def test_actual_standalone_cli_returns_sources_and_rejects_disabled_write(business):
    repo, _, _ = business
    command = [sys.executable, '-m', 'aitown.skills', 'run', 'memory_query', '{"topic":"学习"}',
               '--database', str(repo.path), '--scope', 'synthetic']
    response = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert response.returncode == 0, response.stderr
    result = json.loads(response.stdout)
    assert result['ok'] and result['data']['entries'][0]['evidence'][0]['message_id'] == 1
    arguments = repo.path.parent/'query-arguments.json'
    arguments.write_text('{"topic":"学习"}', encoding='utf-8-sig')
    from_file = subprocess.run([sys.executable, '-m', 'aitown.skills', 'run', 'memory_query', '--arguments-file', str(arguments),
        '--database', str(repo.path), '--scope', 'synthetic'], capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert from_file.returncode == 0 and json.loads(from_file.stdout)['data']['count'] == 1
    disabled = subprocess.run([sys.executable, '-m', 'aitown.skills', 'run', 'memory_correction', '{"fact_id":1,"value":"茶"}',
        '--database', str(repo.path), '--scope', 'synthetic'], capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert disabled.returncode == 1 and not json.loads(disabled.stdout)['ok']


def test_incomplete_correction_requests_id_and_value(business, tmp_path):
    agent = AnotherMeAgent(config_at(tmp_path, correction=True))
    try:
        reply = agent.ask('这条记忆不对', scope='synthetic')
        assert reply.stop_reason == 'clarification' and 'ID' in reply.answer
        assert reply.skills == [] and agent.memory.conn.execute('SELECT count(*) FROM source_records').fetchone()[0] == 0
    finally:
        agent.close()


def test_known_gap_whitespace_is_not_a_new_unanswered_task():
    from aitown.models import AnswerPayload, SkillResult
    from aitown.nodes.checks import unanswered_parts
    state = {'skills': [SkillResult(name='dialogue_summary', ok=True, data={'missing_parts': ['事件 1 消息 3：压缩缺失']})]}
    assert unanswered_parts(AnswerPayload(answer='压缩缺失已说明', missing_parts=['事件1消息3：压缩缺失']), state) == []
    assert unanswered_parts(AnswerPayload(answer='另一任务缺失', missing_parts=['事件1消息4：压缩缺失']), state)
