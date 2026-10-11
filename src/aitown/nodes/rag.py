import re

from ..models import Citation, ExecutionStep
from ..retrieval import SearchFilter
from .common import add_error, work


def filters_for(state):
    decision = state['decision']
    if decision.purpose == 'personal' or state.get('personal_query'):
        return SearchFilter(purpose='personal', subject=decision.subject or 'self', scope=state.get('style_scope'),
                            as_of=decision.as_of, time_from=decision.time_from, time_to=decision.time_to,
                            category='preference' if re.search(r'喜欢(?:喝|吃)?什么|偏好(?:是什么|有哪些|如何)|我的[^，。？?]*兴趣|我对[^，。？?]*(?:感兴趣|感冒)',state['input']) else None)
    return SearchFilter()


def citations(result):
    return [Citation(source=h.item.key, chunk=h.item.ordinal, text=h.item.text, score=h.score,
            source_ids=[s.message_id for s in h.item.sources if s.message_id is not None],
            source_record_ids=[s.record_id for s in h.item.sources if s.record_id is not None],
            subject=h.item.subject or None, timestamp=h.item.timestamp,
            occurred_at=h.item.timestamp if h.item.kind == 'memory' else None, recorded_at=h.item.recorded_at,
            valid_from=h.item.valid_from, valid_to=h.item.valid_to, rerank_score=h.rerank_score) for h in result.hits]


def read(agent, state, query):
    filters = filters_for(state)
    personal = filters.purpose == 'personal'
    result = None
    if agent.retriever:
        result = agent.retriever.search(query, filters)
        state['warnings'].extend(result.warnings)
        try:
            agent.memory.put_transient(state['session'], state['agent'].id, 'retrieval', state['request_id'], result.model_dump())
        except Exception as exc:
            add_error(state, exc, 'retrieval_cache', 'database')
        if result.status == 'retrieval_error':
            from ..errors import AgentError
            add_error(state, AgentError('retrieval', 'rag', 'search_failed', '检索故障，无法确认本轮资料。'), 'rag')
        if personal or result.status == 'ok':
            return {'sources': citations(result), 'retrieval_status': result.status,
                    'personal_query': personal, 'retrieval_details': {**result.model_dump(), 'filters': filters.model_dump()}}
    if agent.retrieval_error:
        from ..errors import AgentError
        add_error(state, AgentError('retrieval', 'rag', 'initialization', '检索初始化失败：'+agent.retrieval_error), 'rag')
    if personal:
        return {'sources': [], 'personal_query': True,
                'retrieval_status': 'retrieval_error' if agent.retrieval_error else 'no_personal_data',
                'retrieval_details': {'filters': filters.model_dump()}}
    agent.knowledge.refresh()
    sources = agent.knowledge.search(query)
    return {'sources': sources, 'personal_query': False,
            'retrieval_status': 'ok' if sources else 'retrieval_error' if agent.retrieval_error or (result and result.status == 'retrieval_error') else 'no_match',
            'retrieval_details': {'semantic_attempt': result.model_dump() if result else None, 'fallback': 'legacy-txt-md'}}


def run(agent, state):
    state = work(state)
    supplemental = state.get('needs_supplement', False)
    query = state.get('supplement_query') if supplemental else state['decision'].retrieval_query or state['decision'].query
    attempt = state.get('retrieval_attempts', 0)+1
    try:
        result = read(agent, state, query)
    except Exception as exc:
        add_error(state, exc, 'rag', 'retrieval')
        result = {'sources': [], 'retrieval_status': 'retrieval_error', 'retrieval_details': {},
                  'personal_query': state['decision'].purpose == 'personal'}
    state['execution'].append(ExecutionStep(node='rag', attempt=attempt,
        status='failed' if result['retrieval_status'] == 'retrieval_error' else 'ok',
        reason=('自省补检：' if supplemental else '计划检索：')+result['retrieval_status']))
    return {**result, 'warnings': state['warnings'], 'errors': state['errors'],
            'execution': state['execution'], 'trace': [*state['trace'], 'rag'],
            'retrieval_attempts': attempt, 'needs_supplement': False,
            'plan_cursor': state.get('plan_cursor', 0)+(0 if supplemental else 1)}


def current_sources(agent, state):
    """Validate current source records without a second vector/model search."""
    filters = filters_for(state)
    current = {item.key: item for item in agent.retriever.current(filters.purpose)} if agent.retriever else {}
    live, removed = [], []
    for source in state['sources']:
        item = current.get(source.source)
        if source.source.startswith('question:'):
            valid = agent.question_bank.current_citation(source)
        elif source.source.startswith(('message:', 'statement:')):
            from ..skills.business import current_citation
            valid = current_citation(agent.retrieval_repository or agent.runtime_repository,
                                     state.get('style_scope', ''), state['agent'].id, source)
        elif item is not None:
            valid = agent.retriever.allowed(item, filters) and item.text == source.text and item.ordinal == source.chunk
            valid = valid and set(source.source_ids) == {s.message_id for s in item.sources if s.message_id is not None}
            valid = valid and set(source.source_record_ids) == {s.record_id for s in item.sources if s.record_id is not None}
        elif source.source.startswith(('fact:', 'memory:', 'document:')):
            valid = False
        else:
            valid = agent.knowledge.current(source)
        (live if valid else removed).append(source)
    return live, removed
