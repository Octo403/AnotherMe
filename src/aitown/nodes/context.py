import time
from ..models import Citation, ExecutionStep
from ..style import ProfileStore, StyleRetriever
from ..style.feedback import load_feedback
from .common import add_error


def run(agent, state):
    warnings = []
    diagnostics = {'warnings': warnings, 'errors': []}
    saved = True
    statement = state.get('user_statement')
    if statement:
        try:
            agent.memory.record_statement(statement, state['input'], state['session'], state['agent'].id, state['request_id'])
        except Exception as exc:
            add_error(diagnostics, exc, 'user_statement', 'database')
            saved = False
    styles, claims = [], []
    feedback = {}
    if agent.style_repository and state.get("style_scope") and not state.get('question_course'):
        try:
            claims = [c for c in ProfileStore(agent.style_repository).active(agent.style_profile,state["style_scope"]) if c.basis=="observed"]
            semantic = agent.retriever if (agent.retrieval_repository and
                agent.retrieval_repository.path.resolve() == agent.style_repository.path.resolve()) else None
            styles = StyleRetriever(agent.style_repository, semantic).search(state["input"],scope=state["style_scope"],scene=state.get("response_scene"))
            feedback = load_feedback(agent.settings.style_feedback,agent.style_repository,scope=state["style_scope"],question=state["input"],scene=state.get("response_scene"))
        except Exception as exc:
            add_error(diagnostics, exc, 'style_context', 'retrieval')
    try:
        bundle = agent.memory.history_bundle(state["session"], state["agent"].id)
    except Exception as exc:
        bundle = {'history': [], 'history_source_ids': [], 'history_summary': {}, 'summary_source_ids': []}
        add_error(diagnostics, exc, 'history_context', 'database')
    try:
        agent.memory.put_transient(state['session'], state['agent'].id, 'parameters', state['request_id'],
                                  {'scene': state.get('response_scene'), 'started_at': state['started_at']})
    except Exception as exc:
        add_error(diagnostics, exc, 'context_cache', 'database')
    if agent.model and not agent._resuming and hasattr(agent.model, 'events'):
        agent.model.events.clear()
    return {**bundle, "sources": [], "skills": [], 'decision': None, 'reply': None,
            'context_expires_at': time.time()+agent.settings.context_ttl_seconds,
            "retrieval_status": None, "retrieval_details": {}, "personal_query": False,
            "warnings": warnings, "trace": ["context"],
            "original_question": state["input"], "rewritten_question": state["input"],
            "style_examples": [s.guidance for s in styles],
            "profile_evidence": [Citation(source=f"trait:{c.trait_id}",chunk=c.trait_id,text=c.text,score=1,source_ids=c.source_ids) for c in claims],
            "style_details": styles, "profile_details": claims, "tool_calls": [],
            "feedback_details": feedback,
            "correction_count": 0, 'statement_saved': saved,
            'plan_cursor': 0, 'supplemental_count': 0, 'retrieval_attempts': 0,
            'needs_supplement': False, 'supplement_query': '', 'references': [],
            'reviews': [], 'draft_answer': None, 'execution': [ExecutionStep(node='context')],
            'errors': diagnostics['errors'], 'model_calls': [], 'stop_reason': '', 'resume_refreshed': False}

