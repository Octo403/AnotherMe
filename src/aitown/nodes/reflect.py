import json
import time

from ..errors import AgentError
from ..models import AnswerPayload, CheckIssue, ExecutionStep, Reply, ReviewPayload, ReviewRecord
from ..runtime.prompt import PRIORITY, messages as context_messages
from ..style import compose_prompt
from ..style.feedback import format_feedback_answer
from . import rag
from .checks import check, unanswered_parts
from .common import FALLBACK, add_error, call_model, demo_answer, work

CLAIM_RULES = (
    'claims.subject 只能用 general/self/task/other 四种角色，不能填写数学、活动名或地点名。'
    '文档事实和工具结果用 general，会话方案参数用 task，本人事实用 self；'
    '他人事实用 other，subject_id 必须等于资料的 subject 原值（如 other:hx）。'
    'memory_query/dialogue_summary 工具里的本人计划或经历仍用 self，他人用 other，绝不能标成 task/general。'
    '每条个人 claims.source_refs 必须单独填写其来源，顶层 source_refs 不替代单条引用。'
    '用户问句的肯定前提不是事实；来源说不喜欢某物时不能因“是不是喜欢”而改成喜欢。对同一来源里的不同对象分别保留肯定和否定。'
    'source_refs 只填写 evidence.sources 中 ref 原值；没有文档或个人资料则为空。'
    'answer 正文用自然语言，不自行翻译或拼接来源编号；技术引用编号集中放入 source_refs 字段。'
    '正文不出现 #编号 或“文档1:0#0”这类引用文字；回答的依据只通过结构化 source_refs 提供。'
    '工具只通过 claims.tool_refs 的整数编号引用，绝不能在 source_refs 填 calculator:0 或 event_parser:0:0。'
    '数量相乘的结论必须引用 calculator 的实际 ref，不能只引用提供数量的 dialogue_summary；数学结论用 general，source_refs 为空。'
    '工具返回的数据本身就是证据，工具有成功结果时不要因缺少文档而要求补检。'
    '工具 missing_parts 中的压缩正文缺失、未复核或截断是已知资料缺口；在 answer 明示这些缺口即可，'
    '不要把它们放进顶层 missing_parts，也不要为不可恢复的正文请求补检。'
    'dialogue_summary 的摘录正文由程序附在最终回答；根据 sources 核对原话，概括并回答计算等其余问题即可。'
    'question_search/question_get 的完整原题由程序附加；只说明匹配理由、覆盖和缺口，不改写数学条件，不生成解答。'
    '题库属于 general 外部资料，不能推断本人已经做过题或正在修这门课。'
)


def evidence(state):
    tools = []
    for i, result in enumerate(state['skills']):
        data = result.model_dump()
        if result.ok and result.name in ('memory_query', 'dialogue_summary', 'memory_correction'):
            data['data'] = {key: value for key, value in result.data.items() if key != 'evidence'}
            if result.name == 'memory_query':
                data['data']['entries'] = [{key: value for key, value in entry.items() if key != 'evidence'} for entry in result.data['entries']]
            if result.name == 'dialogue_summary':
                # Full original messages already appear in sources; the formatted
                # transcript is appended by include_summary, not sent twice.
                data['data'].pop('content',None)
        tools.append({'ref': i, **data})
    return {'sources': [{'ref':source.ref,'text':source.text,'subject':source.subject,
                         **{key:value for key in ('timestamp','recorded_at','occurred_at','valid_from','valid_to')
                            if (value:=getattr(source,key)) is not None}} for source in state['sources']],
            'tools': tools}


def style_prompt(state, identity):
    return compose_prompt(identity, claims=state['profile_details'][:6], examples=state['style_details'][:3],
                          feedback=state['feedback_details'], question=state['input'], scene=state.get('response_scene'))


def unknown_answer(state):
    by_status = {'ambiguous_scope': '请先用 --scope 指定已确认的资料关系范围，再查询个人记忆。',
                 'inference_only': '只找到推测或待确认记录，不能据此确认经历。',
                 'retrieval_error': '个人资料检索发生故障，本轮无法确认该经历。'}
    answer = by_status.get(state.get('retrieval_status'), '没有查到有依据的个人资料，我不知道这段经历。')
    if state['skills']:
        answer += '\n'+demo_answer({**state, 'sources': []})
    return answer


def unverified_answer(state):
    if state.get('personal_query') and state['sources']:
        return '找到了相关资料，但本轮生成的回答未通过校验，暂时无法给出可靠结论。'
    return unknown_answer(state) if state.get('personal_query') else demo_answer(state)


def include_summary(payload, state):
    """Render the requested extractive result, including its known gaps."""
    summaries = [result.data['content'] for result in state['skills'] if result.ok and result.name == 'dialogue_summary']
    additions = [text for text in summaries if text not in payload.answer]
    if not additions:
        return payload
    from ..skills.business import citations
    refs = [s.ref for result in state['skills'] if result.name == 'dialogue_summary' for s in citations(result)]
    return payload.model_copy(update={'answer': payload.answer+'\n\n'+'\n'.join(additions),
                                      'source_refs': list(dict.fromkeys([*payload.source_refs, *refs]))})


def refresh_sources(agent, state):
    live, removed = rag.current_sources(agent, state)
    from ..skills import SkillContext
    from ..skills.business import citations
    from ..models import SkillResult
    context = SkillContext(agent.retrieval_repository or agent.runtime_repository, scope=state.get('style_scope', ''),
                           question=state['input'], session=state['session'], agent_id=state['agent'].id)
    stale = set()
    for ordinal, result in enumerate(state['skills']):
        if result.ok and result.name in ('memory_query', 'dialogue_summary', 'question_search', 'question_get') and ordinal < len(state['tool_calls']):
            if result.name in ('question_search','question_get'):
                # Source refresh checks records, not a second BGE/ChatGPT call.
                from ..question_bank.models import GetArgs
                fresh=agent.question_bank.get(GetArgs(question_ids=result.data['question_ids'],
                    include_answer=bool(state['tool_calls'][ordinal].arguments.get('include_answer',False)))) if result.data['question_ids'] else None
                previous=[{k:v for k,v in q.items() if k not in ('score','match_reason')} for q in result.data['results']]
                differs=bool(fresh and fresh.results!=previous)
            else:
                fresh = agent.skills.execute(state['tool_calls'][ordinal], context)
                differs = fresh != result
            if differs:
                stale.update(s.ref for s in citations(result))
                state['skills'][ordinal] = SkillResult(name=result.name, ok=False, error='资料或查询状态已变化，请重新查询；旧结果已移除。')
    if stale:
        removed = list({s.ref: s for s in [*removed, *(s for s in live if s.ref in stale)]}.values())
        live = [s for s in live if s.ref not in stale]
    if removed:
        state['sources'] = live
        refs = {s.ref for s in removed}
        state['skills'] = [SkillResult(name=result.name, ok=False, error='技能引用资料已变化，旧结果已移除。')
                          if any(s.ref in refs for s in citations(result)) else result for result in state['skills']]
        state['retrieval_details'] = {'status': 'source_changed', 'removed_refs': [s.ref for s in removed]}
        if not live:
            state['retrieval_status'] = 'no_personal_data' if state.get('personal_query') else 'no_match'
        state['reviews'] = [ReviewRecord(round=record.round, draft=AnswerPayload(answer='引用资料已变化，草稿已失效。'),
            deterministic_issues=[CheckIssue(code='source_changed', message='此前草稿引用的资料发生变化。')], changed=True)
            for record in state['reviews']]
        state['draft_answer'] = '引用资料已变化，草稿已失效。' if state.get('draft_answer') else None
        state['warnings'] = [info.message for info in state['errors']]+['本轮资料已更新或删除，旧引用已移除。']
    return bool(removed)


def supplement(state, query, reason):
    state.update(needs_supplement=True, supplement_query=query or state['decision'].retrieval_query or state['decision'].query,
                 supplemental_count=state.get('supplemental_count', 0)+1, reply=None)
    state['execution'].append(ExecutionStep(node='reflect', attempt=len(state['reviews'])+1, status='retry', reason=reason))
    state['trace'].append('reflect')
    return state


def run(agent, state):
    state = work(state)
    finished = agent.memory.finished(state['request_id'])
    if finished:
        return {'reply': finished, 'correction_count': finished.correction_count, 'needs_supplement': False}
    if agent._resuming and not state.get('resume_refreshed'):
        if state.get('context_expires_at', 0) <= time.time():
            state['response_scene'] = None
        refreshed = agent.context(state)
        for key in ('history', 'history_source_ids', 'history_summary', 'summary_source_ids', 'style_examples',
                    'profile_evidence', 'style_details', 'profile_details', 'feedback_details', 'context_expires_at'):
            state[key] = refreshed[key]
        state['warnings'].extend(refreshed['warnings'])
        state['errors'].extend(refreshed['errors'])
        if 'rag' in state['decision'].plan or state['decision'].intent == 'knowledge':
            try:
                state.update(rag.read(agent, state, state['decision'].retrieval_query or state['decision'].query))
                state['retrieval_attempts'] = state.get('retrieval_attempts', 0)+1
                state['execution'].append(ExecutionStep(node='rag', attempt=state['retrieval_attempts'], reason='恢复后刷新只读证据'))
            except Exception as exc:
                add_error(state, exc, 'resume_retrieval', 'retrieval')
                state.update(sources=[], retrieval_status='retrieval_error')
        if state['decision'].calls and any(call.name in ('memory_query', 'dialogue_summary') for call in state['decision'].calls):
            from . import skill
            refreshed_skills = skill.run(agent, {**state, 'sources': [s for s in state['sources'] if not s.source.startswith(('message:', 'statement:'))]})
            for key in ('skills', 'tool_calls', 'sources', 'warnings', 'errors', 'model_calls', 'execution', 'personal_query', 'retrieval_status'):
                if key in refreshed_skills:
                    state[key] = refreshed_skills[key]
        state['resume_refreshed'] = True
    changed_sources = False
    if state['sources']:
        try:
            changed_sources = refresh_sources(agent, state)
        except Exception as exc:
            add_error(state, exc, 'source_check', 'retrieval')
            state.update(sources=[], retrieval_status='retrieval_error', retrieval_details={})
    decision = state['decision']
    if changed_sources and 'rag' in state['decision'].plan and state.get('supplemental_count', 0) < 1:
        return supplement(state, decision.retrieval_query, '引用资料已变化，按原问题补检一次')
    answer, stop, refs, generated = '', 'answered', [], False
    blocked = state.get('personal_query') and state.get('retrieval_status') != 'ok'
    if decision.needs_clarification:
        answer, stop = decision.clarification_question, 'clarification'
    elif state.get('statement_saved') is False:
        answer, stop = '本轮自述或纠正未成功保存，请查看错误并核对事实 ID 与资料范围。', 'database_failed'
    elif state['skills'] and any(not result.ok for result in state['skills']) and any(call.name in ('memory_query', 'dialogue_summary', 'memory_correction') for call in decision.calls):
        answer, stop = demo_answer(state), 'skill_failed'
    elif blocked:
        answer, stop = unknown_answer(state), 'retrieval_failed' if state.get('retrieval_status') == 'retrieval_error' else 'no_evidence'
    elif decision.calls and all(c.name in ('question_search','question_get','course_status') for c in decision.calls):
        # Retrieval is an exact source display. A free-form answer model would
        # introduce a second opportunity to change conditions or solve a hidden
        # answer. Similarity reasoning is already returned by the requested
        # ChatGPT classifier and is rendered with the original questions.
        answer = demo_answer(state)
        refs = [s.ref for s in state['sources']]
    elif not agent.model:
        answer = demo_answer(state)
        refs = [s.ref for s in state['sources']]
    else:
        draft = None
        try:
            profile = state['agent']
            prompt = style_prompt(state,
                f'你是 {profile.name}；角色：{profile.persona}；语言：{profile.language}。'
                '生成 AnswerPayload JSON。answer 回答原问题全部部分，风格仅参考表达形式。'
                '事实断言的 claims.text 使用 answer 中的原句。'+CLAIM_RULES+
                'source_refs 使用资料 ref 原值；tool_refs 是成功工具的整数 ref。'
                '本人经历/偏好只引用本人来源，具体对象沿用来源原文；发生时间与记录时间不同。'
                '工具失败不能写成成功。缺少的部分写入 missing_parts；会话任务参数的 subject 为 task。')
            draft = call_model(agent, state, context_messages(prompt, state['history'], state['history_summary'],
                               evidence(state), decision.query), AnswerPayload, 'draft')
            state['draft_answer'] = state.get('draft_answer') or draft.answer
        except Exception as exc:
            add_error(state, exc, 'draft')
            answer, stop = demo_answer(state), 'model_failed'
        if draft:
            issues = check(include_summary(draft, state), state)
            record = ReviewRecord(round=len(state['reviews'])+1, draft=draft, deterministic_issues=issues)
            try:
                review = call_model(agent, state, [
                    {'role': 'system', 'content':
                     '执行第二阶段自省，返回 ReviewPayload JSON。逐条核对确定性检查、事实支持、主体、否定、发生/记录时间、'
                     '真实工具结果和遗漏的问题。先阅读本轮证据，再修正草稿，不能只写 corrections 却留下原错误。'
                     'answer 只回答原问题，不复述草稿里无关的捏造经历，也不叙述纠错过程；纠错说明放入 corrections。'
                     '缺少证据时 needs_retrieval=true，retrieval_query 只补原问题缺失部分；不要提出新任务。'
                     'source_refs 和 claims 也必须随最终 answer 更新。claims.text 用最终回答原句。'
                     +CLAIM_RULES+
                     '无事实/对象/逻辑错误时原样保留合适短语、分行、语气，不添加套话；不为风格牺牲事实。'+PRIORITY+
                     style_prompt(state, '沿用本轮表达要求。')},
                    {'role': 'user', 'content': json.dumps({'question': state['input'], 'rewritten_question': decision.query,
                     'draft': draft.model_dump(), 'deterministic_issues': [i.model_dump() for i in issues],
                     'evidence': evidence(state), 'history_data': state['history'], 'summary_data': state['history_summary'],
                     'query_constraints': {'subject': decision.subject, 'as_of': decision.as_of, 'time_from': decision.time_from, 'time_to': decision.time_to}}, ensure_ascii=False)}], ReviewPayload, 'review')
                record.model_review = review
                final = include_summary(AnswerPayload(answer=review.answer, source_refs=review.source_refs, claims=review.claims, missing_parts=review.missing_parts), state)
                record.final_issues = check(final, state)
                record.changed = review.answer != draft.answer or bool(review.corrections)
                state['warnings'].extend(review.corrections)
                removed = refresh_sources(agent, state) if state['sources'] else False
                if removed:
                    # Deleted source text must not survive in returned draft or
                    # reviewer prose; only the failed-check reason is retained.
                    record = ReviewRecord(round=record.round, draft=AnswerPayload(answer='引用资料已变化，草稿已失效。'),
                        deterministic_issues=[CheckIssue(code='source_changed', message='生成期间引用资料发生变化。')],
                        final_issues=[CheckIssue(code='source_changed', message='旧草稿不可作为当前答案。')], changed=True)
                    state['draft_answer'] = record.draft.answer
                state['reviews'].append(record)
                needs = review.needs_retrieval or not review.evidence_sufficient or bool(unanswered_parts(review, state)) or removed
                if needs and state.get('supplemental_count', 0) < 1:
                    return supplement(state, decision.retrieval_query if removed else review.retrieval_query,
                                      '自省发现原任务证据缺口，最多补检一次')
                if needs or record.final_issues:
                    answer, stop = unverified_answer(state), 'evidence_insufficient' if needs else 'validation_failed'
                    state['warnings'].extend(i.message for i in record.final_issues)
                else:
                    answer, generated = final.answer, True
                    refs = list(dict.fromkeys([*final.source_refs, *(ref for claim in final.claims for ref in claim.source_refs)]))
            except Exception as exc:
                add_error(state, exc, 'review')
                state['reviews'].append(record)
                answer, stop = unverified_answer(state), 'review_failed'
    if generated:
        answer = format_feedback_answer(answer, state['feedback_details'])
        from ..question_bank.conversation import render_results
        questions = render_results(state['skills'])
        if questions:
            answer += '\n\n'+questions
    if 'rag' in decision.plan and not state['sources'] and not blocked and stop != 'clarification' and FALLBACK not in answer:
        answer = FALLBACK+'\n'+answer
    if any(not result.ok for result in state['skills']):
        state['warnings'].append('部分技能执行失败；本轮没有将失败当成成功。')
        if stop in ('answered', 'no_evidence'):
            stop = 'skill_failed'
    elif 'skill' in decision.plan and not state['skills'] and stop == 'answered':
        stop = 'skill_failed'
    if state.get('retrieval_status') == 'retrieval_error' and stop == 'answered':
        stop = 'retrieval_failed'
    if not refs and state['sources'] and not generated:
        refs = [s.ref for s in state['sources']]
    correction_count = sum(record.changed for record in state['reviews'])
    state['execution'].append(ExecutionStep(node='reflect', attempt=len(state['reviews']) or 1,
        status='clarification' if stop == 'clarification' else 'failed' if stop.endswith('failed') or stop in ('validation_failed', 'evidence_insufficient') else 'ok', reason=stop))
    state['trace'].append('reflect')
    reply = Reply(agent=state['agent'].name, original_question=state['original_question'], rewritten_question=state['rewritten_question'],
        answer=answer, intent=decision.intent, mode=agent.settings.mode, sources=state['sources'], skills=state['skills'],
        trace=state['trace'], warnings=list(dict.fromkeys(state['warnings'])), style_examples=state['style_examples'],
        profile_evidence=state['profile_evidence'], tool_calls=state['tool_calls'], correction_count=correction_count,
        retrieval_status=state.get('retrieval_status'), retrieval_details=state.get('retrieval_details', {}),
        history_source_ids=state['history_source_ids'], summary_source_ids=state['summary_source_ids'],
        decision=decision, references=state.get('references', []), draft_answer=state.get('draft_answer'), reviews=state['reviews'],
        execution=state['execution'], errors=state['errors'], model_calls=state['model_calls'],
        supplemental_count=state.get('supplemental_count', 0), stop_reason=stop, answer_source_refs=refs)
    state['reply'] = agent.memory.complete(state['request_id'], reply, state['agent'].id, agent.settings.mode)
    state.update(correction_count=correction_count, needs_supplement=False, stop_reason=stop)
    return state
