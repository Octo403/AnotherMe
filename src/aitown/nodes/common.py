import json
import time

from ..errors import AgentError, error_info
from ..models import ModelCall

FALLBACK = "该内容未查询到本地知识库信息。"


def work(state):
    return {**state, **{key: list(state.get(key, [])) for key in
            ('warnings', 'errors', 'model_calls', 'execution', 'trace', 'reviews')}}


def add_error(state, exc, phase, category='model', warning=True):
    info = error_info(exc, phase, category)
    state['errors'].append(info)
    if warning:
        state['warnings'].append(info.message)
    return info


def call_model(agent, state, messages, schema, phase):
    # Keep whole recent messages. Never cut a user's current request or evidence
    # halfway through a negation/date; older turns are the first to be omitted.
    limit = agent.settings.context_max_chars
    bounded = list(messages)
    while sum(len(m['content']) for m in bounded) > limit and len(bounded) > 3:
        if len(bounded) > 4 and bounded[1]['role'] == 'user' and bounded[2]['role'] == 'assistant':
            del bounded[1:3]
        else:
            del bounded[1]
    if sum(len(m['content']) for m in bounded) > limit:
        raise AgentError('model', phase, 'context_length', '本轮必要资料超过上下文长度，请缩小问题或资料范围。')
    if len(bounded) != len(messages):
        state['warnings'].append('上下文长度受限，本轮省略部分较早历史；原文仍可查询。')
    events = getattr(agent.model, 'events', [])
    before = len(events)
    started = time.perf_counter()
    failed = False
    try:
        evidence_model = getattr(agent.model, 'structured_evidence', None)
        if evidence_model and phase in ('draft', 'review'):
            if getattr(agent.model, 'supports_tool_reference_schema', False):
                return evidence_model(bounded, schema, state['sources'], tools=state['skills'])
            return evidence_model(bounded, schema, state['sources'])
        return agent.model.structured(bounded, schema)
    except Exception:
        failed = True
        raise
    finally:
        if len(events) > before:
            state['model_calls'].extend(event.model_copy(update={'phase': phase}) for event in events[before:])
        else:
            state['model_calls'].append(ModelCall(phase=phase, seconds=time.perf_counter()-started,
                                                  status='failed' if failed else 'ok', provider_request=False))


def demo_answer(state):
    prefix = f"[离线演示 · {state['agent'].name}] "
    from ..question_bank.conversation import render_results
    questions = render_results(state['skills'])
    if questions:
        return f"[题库结果 · {state['agent'].name}] "+questions
    if state['skills'] and state['sources']:
        tools = '\n'.join(json.dumps(r.data, ensure_ascii=False) if r.ok else f'技能失败：{r.error}' for r in state['skills'])
        sources = '\n'.join(f'[{c.ref}] {c.text}' for c in state['sources'])
        return prefix+'真实工具结果：\n'+tools+'\n检索原文（未生成角色回答）：\n'+sources
    if state["skills"]:
        return prefix + "\n".join(
            json.dumps(r.data, ensure_ascii=False) if r.ok else f"技能失败：{r.error}"
            for r in state["skills"])
    if state["sources"]:
        return prefix + "以下是检索到的原文，尚未由大模型生成角色回答：\n" + "\n".join(
            f"[{c.source}#{c.chunk}] {c.text}" for c in state["sources"])
    if state["decision"].intent == "chat":
        return prefix + f"你好，我是{state['agent'].name}。{state['agent'].persona}"
    return prefix + "离线模式不生成通用知识答案，请接入模型后继续讨论。"

