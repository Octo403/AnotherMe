"""T10 live measurements against disposable synthetic records and local Qwen."""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from aitown.graph import AnotherMeAgent
from aitown.config import Settings
from aitown.memory.models import Evidence, Memory
from aitown.memory.repository import MemoryStore, review_record
from aitown.models import Decision, ResultBinding, SkillCall
from aitown.runtime import UserStatement
from aitown.runtime.checkpoints import TurnPaused
from aitown.skills import SkillContext
from aitown.skills.business import BEIJING
from verify_context import live_settings


def seed(agent):
    repo = agent.runtime_repository
    stamp = int(datetime(2026, 9, 15, 10, tzinfo=BEIJING).timestamp())
    with repo.conn:
        repo.conn.execute("INSERT INTO import_batches VALUES('t10-synthetic','synthetic',0,0,0,'ready')")
        for i, (content, speaker, status) in enumerate([
            ('我计划十月复习线性代数，还没开始', 'self', 'text'),
            ('你什么时候复习？', 'peer:hx', 'text'), ('[压缩正文缺失]', 'self', 'compressed_unresolved')], 1):
            repo.conn.execute('INSERT INTO message_refs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (i, 't10-synthetic', i, 'synthetic', 'hx', stamp+i, 'outgoing' if speaker == 'self' else 'incoming', speaker, 1, status, content, 'approved'))
        repo.conn.execute("INSERT INTO episodes VALUES(1,'t10-synthetic','e1','synthetic',?,?,'[]')", (stamp, stamp+3))
        repo.conn.executemany('INSERT INTO episode_messages VALUES(1,?,?)', [(i, i-1) for i in (1, 2, 3)])
    memory = Memory(entry_key='t10-plan', episode_id=1, category='plan', subject='self', scope='synthetic', split='train',
        basis='direct', recorded_at=stamp+1, occurred_at=int(datetime(2026, 10, 15, tzinfo=BEIJING).timestamp()),
        value='我计划十月复习线性代数，还没开始', summary='九月提出十月复习线性代数的计划，尚未开始',
        evidence=[Evidence(message_id=1, batch_id='t10-synthetic', source_id=1, timestamp=stamp+1, direction='outgoing',
                           speaker_id='self', excerpt='我计划十月复习线性代数，还没开始')])
    MemoryStore(repo).save([memory], {memory.entry_key: review_record(memory)})
    return agent.memory.record_statement(UserStatement(attribute='preference', value='咖啡', scope='synthetic'),
                                         '我喜欢咖啡', 'preference', agent.settings.agent.id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('data/private/t10/verification'))
    args = parser.parse_args()
    output = args.output/time.strftime('run-%Y%m%d-%H%M%S')
    output.mkdir(parents=True)
    cfg = live_settings(output)
    installed = Settings.from_env(mode='live')
    cfg.local_embedding_dir = installed.local_embedding_dir
    cfg.local_reranker_dir = installed.local_reranker_dir
    cfg.enable_memory_correction = True
    cfg.max_tokens = 1200
    cfg.temperature = 0
    report = {'run': str(output), 'synthetic_only': True, 'cases': {}, 'checks': {}}
    responses = {}
    agent = AnotherMeAgent(cfg)
    started = time.perf_counter()
    try:
        fact = seed(agent)
        questions = {
            'query': '上个月我提过哪些学习计划？',
            'query_summary': '上个月我提过哪些学习计划，并整理成时间线',
            'three_steps': '上个月我提过哪些学习计划，整理成时间线，再计算可读消息数乘以15',
            'summary_calculate': '整理事件1为时间线，并计算可读消息数乘以15',
            'model_selected_chain': '从我的已整理资料中找出有关复习的安排，按时间顺序摘录对应对话。',
            'correction': f'这条记忆不对，纠正事实 {fact}：不喜欢咖啡，喜欢茶',
            'after_correction': '我喜欢咖啡还是茶？',
            'no_result': '查询记忆，主题：不存在的航空驾驶经历',
        }
        for name, question in questions.items():
            reply = agent.ask(question, session=name, scope='synthetic')
            responses[name] = reply.model_dump()
            report['cases'][name] = {'stop_reason': reply.stop_reason, 'skills': [r.name for r in reply.skills],
                'tool_ok': [r.ok for r in reply.skills], 'http_requests': sum(r.provider_request for r in reply.model_calls),
                'errors': [e.model_dump() for e in reply.errors]}
            print('Measured '+name+' '+reply.stop_reason, flush=True)
        agent.skills.enable('memory_query', False)
        disabled = agent.ask('查询学习计划', session='disabled', scope='synthetic')
        recovered = agent.ask('计算6*7', session='disabled', scope='synthetic')
        responses['disabled'], responses['next_turn'] = disabled.model_dump(), recovered.model_dump()
        agent.skills.enable('memory_query')
        agent.ask('整理事件1为Markdown摘要', session='resume-read', scope='synthetic', interrupt_after=['skill'])
    except TurnPaused:
        agent.close()
        agent = AnotherMeAgent(cfg)
        resumed = agent.resume('resume-read')
        responses['resume_read'] = resumed.model_dump()
        report['checks']['read_chain_resumes_after_restart'] = resumed.stop_reason == 'answered' and resumed.skills[0].ok
    finally:
        if responses:
            cases = ['query', 'query_summary', 'three_steps', 'summary_calculate', 'model_selected_chain', 'correction', 'after_correction']
            for name in cases:
                value = responses.get(name, {})
                report['checks'][name+'_answered_and_final_check_passed'] = value.get('stop_reason') == 'answered' and bool(value.get('reviews')) and value['reviews'][-1]['final_issues'] == []
            report['checks']['query_recorded_month_not_occurrence_month'] = bool(responses.get('query', {}).get('skills')) and responses['query']['skills'][0]['data'].get('count') == 1
            report['checks']['three_typed_steps_count_30'] = [r['name'] for r in responses.get('three_steps', {}).get('skills', [])] == ['memory_query', 'dialogue_summary', 'calculator'] and responses['three_steps']['skills'][-1]['data'].get('value') == 30
            report['checks']['model_independently_selected_query_and_summary'] = [r['name'] for r in responses.get('model_selected_chain', {}).get('skills', [])] == ['memory_query', 'dialogue_summary']
            report['checks']['plan_not_reported_completed'] = all('已完成线性代数' not in responses.get(name, {}).get('answer', '') for name in ('query', 'query_summary', 'three_steps'))
            report['checks']['missing_compressed_record_preserved'] = bool(responses.get('query_summary', {}).get('skills')) and any('未补造' in part for part in responses['query_summary']['skills'][-1]['data'].get('missing_parts', []))
            report['checks']['correction_old_fact_superseded'] = agent.memory.conn.execute('SELECT status FROM facts WHERE id=?', (fact,)).fetchone()[0] == 'superseded'
            report['checks']['no_result_is_explicit_without_new_personal_fact'] = responses.get('no_result', {}).get('stop_reason') == 'no_evidence'
            report['checks']['disabled_then_next_turn_success'] = responses.get('disabled', {}).get('stop_reason') == 'skill_failed' and bool(responses.get('next_turn', {}).get('skills')) and responses['next_turn']['skills'][0]['data'].get('value') == 42
            report['checks']['foreign_keys'] = agent.memory.conn.execute('PRAGMA foreign_key_check').fetchall() == []
            calls = [call for response in responses.values() for call in response['model_calls']]
            report.update(seconds=time.perf_counter()-started, http_requests=sum(c['provider_request'] for c in calls),
                input_tokens=sum(c.get('input_tokens') or 0 for c in calls), output_tokens=sum(c.get('output_tokens') or 0 for c in calls))
            (output/'responses.json').write_text(json.dumps(responses, ensure_ascii=False, indent=2), encoding='utf-8')
            (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False, indent=2))
        agent.close()


if __name__ == '__main__':
    main()
