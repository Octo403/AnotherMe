import time
import sqlite3
import traceback

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .errors import AgentError
from .runtime import RuntimeStore, UserStatement
from .runtime.checkpoints import Checkpoints, TurnPaused
from .runtime.models import explicit_statement
from .knowledge import KnowledgeBase
from .llm import CompatibleModel
from .models import AgentState, Reply
from .skills import default_registry
from .storage import Repository
from .style import ProfileStore
from .retrieval.factory import create_retriever

from .nodes import context as context_node, intent as intent_node, rag as rag_node
from .nodes import skill as skill_node, reflect as reflect_node
from .nodes.common import FALLBACK, demo_answer
from .nodes.intent import personal_question




class AnotherMeAgent:
    def __init__(self, settings: Settings):
        if settings.mode not in ("demo", "live"):
            raise ValueError("AITOWN_MODE 只能是 demo 或 live")
        self.settings = settings
        self.model = CompatibleModel(settings) if settings.mode == "live" else None
        runtime_path = settings.runtime_database or settings.retrieval_database or settings.style_database or settings.memory_db.with_suffix('.application.sqlite3')
        self.runtime_repository = Repository(runtime_path)
        self.memory = RuntimeStore(self.runtime_repository, settings.history_turns, settings.ttl_hours, settings.context_ttl_seconds)
        self.memory.import_legacy(settings.memory_db)
        checkpoint_path = settings.checkpoint_db or runtime_path.with_name(runtime_path.stem+'-checkpoints.sqlite3')
        self.checkpoints = Checkpoints(checkpoint_path)
        self._resuming = False
        self.knowledge = KnowledgeBase(settings.knowledge_dir, settings.threshold,
                                      self.model if settings.embedding_model else None)
        self.skills = default_registry(enable_correction=settings.enable_memory_correction)
        from .question_bank.models import BankConfig
        from .question_bank.search import QuestionBank
        from .skills.question_bank import QuestionSearch, QuestionGet, CourseStatus
        self.question_bank = QuestionBank(BankConfig(root=settings.question_bank_dir,
            embedding_dir=settings.local_embedding_dir,reranker_dir=settings.local_reranker_dir,
            similar_provider=settings.question_similar_provider,similar_model=settings.question_similar_model,
            similar_images=settings.question_similar_images,api=settings.question_api,
            extractor=settings.question_extractor,mineru_api_key=settings.mineru_api_key,
            mineru_api_model=settings.mineru_api_model,mineru_python=settings.mineru_python,
            mineru_tier=settings.mineru_tier,pdf_ocr=settings.question_pdf_ocr,
            ocr_columns=settings.question_ocr_columns))
        for skill in (QuestionSearch(self.question_bank), QuestionGet(self.question_bank), CourseStatus(self.question_bank)):
            self.skills.register(skill)
        self.retrieval_repository = None
        self.retriever = None
        self.retrieval_error = None
        if settings.retrieval_database:
            if not settings.retrieval_database.is_file():
                raise ValueError("RETRIEVAL_DATABASE 文件不存在")
            self.retrieval_repository = Repository(settings.retrieval_database)
            try:
                self.retriever = create_retriever(self.retrieval_repository, settings.retrieval_index_dir,
                    settings.local_embedding_dir, settings.local_reranker_dir,
                    threshold=settings.semantic_threshold, rerank_threshold=settings.rerank_threshold,
                    top_n=settings.retrieval_top_n, top_k=settings.retrieval_top_k)
            except Exception as exc:
                self.retrieval_error = type(exc).__name__
        self.style_repository = None
        self.style_profile = None
        if settings.style_database:
            if not settings.style_database.is_file():
                raise ValueError("STYLE_DATABASE 文件不存在")
            self.style_repository = Repository(settings.style_database)
            self.style_profile = ProfileStore.read(settings.style_profile)
        builder = StateGraph(AgentState)
        for name, node in [("context", self.context), ("intent", self.intent),
                           ("rag", self.rag), ("skill", self.skill), ("reflect", self.reflect)]:
            builder.add_node(name, node)
        builder.add_edge(START, "context")
        builder.add_edge("context", "intent")
        for name in ('intent', 'rag', 'skill'):
            builder.add_conditional_edges(name, self.next_step, {'reflect': 'reflect', 'rag': 'rag', 'skill': 'skill'})
        builder.add_conditional_edges('reflect', lambda s: 'rag' if s.get('needs_supplement') else 'end', {'rag': 'rag', 'end': END})
        self.graph = builder.compile(checkpointer=self.checkpoints.saver)

    @staticmethod
    def next_step(state):
        decision = state['decision']
        if decision.needs_clarification:
            return 'reflect'
        plan = decision.plan or (['rag'] if decision.intent == 'knowledge' else ['skill'] if decision.intent == 'skill' else [])
        cursor = state.get('plan_cursor', 0)
        return plan[cursor] if cursor < len(plan) else 'reflect'

    @staticmethod
    def database_error(exc, phase):
        frames = traceback.extract_tb(exc.__traceback__)
        checkpoint = any('/langgraph/checkpoint/' in frame.filename.replace('\\', '/') or
                         frame.filename.replace('\\', '/').endswith('/runtime/checkpoints.py') or
                         'checkpoint' in frame.name for frame in frames)
        category = 'checkpoint' if checkpoint else 'database'
        return AgentError(category, phase, type(exc).__name__, '检查点保存或读取失败，可使用 resume 恢复。' if checkpoint else '应用数据库操作失败，可修复数据库后使用 resume 恢复。')

    def context(self, state):
        return context_node.run(self, state)

    def intent(self, state):
        return intent_node.run(self, state)

    def local_decision(self, query, history):
        return intent_node.local_decision(query, history)

    def rag(self, state):
        return rag_node.run(self, state)

    def skill(self, state):
        return skill_node.run(self, state)

    def reflect(self, state):
        return reflect_node.run(self, state)

    def demo_answer(self, state):
        return demo_answer(state)

    def configuration(self, session):
        return {'configurable': {'thread_id': self.memory.thread_id(session, self.settings.agent.id)}, 'recursion_limit': 20}

    def cleanup_context(self):
        result = self.memory.cleanup()
        removed = 0
        threads = [r[0] for r in self.checkpoints.conn.execute('SELECT DISTINCT thread_id FROM checkpoints')]
        for thread in threads:
            if self.memory.conn.execute("SELECT 1 FROM runtime_runs WHERE thread_id=? AND status='pending'", (thread,)).fetchone():
                continue
            snapshot = self.graph.get_state({'configurable': {'thread_id': thread}})
            completed = self.memory.completed_at(snapshot.values.get('request_id'))
            if (completed or snapshot.values.get('started_at', 0)) < time.time()-self.memory.ttl:
                self.checkpoints.saver.delete_thread(thread)
                removed += 1
        return {**result, 'checkpoint_threads_deleted': removed}

    def ask(self, question: str, session: str = "default", *, scope: str | None = None, scene: str | None = None,
            statement: UserStatement | None = None, question_course: str = '', interrupt_after=None) -> Reply:
        try:
            return self._ask(question, session, scope=scope, scene=scene, statement=statement,
                             question_course=question_course, interrupt_after=interrupt_after)
        except sqlite3.Error as exc:
            raise self.database_error(exc, 'ask') from exc

    def _ask(self, question: str, session: str = 'default', *, scope=None, scene=None, statement=None, question_course='', interrupt_after=None):
        if not question.strip():
            raise ValueError("问题不能为空")
        if self.memory.pending(session, self.settings.agent.id):
            raise ValueError('会话有未完成轮次，请使用 resume 继续，不覆盖未完成输入。')
        from .question_bank.conversation import course_mention
        selected=self.question_bank.resolve_course(question_course) if question_course else course_mention(question,self.question_bank.courses())
        if question_course and not selected:
            raise ValueError('请选择已有的课程题库。')
        question_course = selected
        scope = self.settings.style_scope if scope is None else scope
        if question_course and statement:
            raise ValueError('固定题库时不能写入个人资料，请先取消固定。')
        statement = statement or (explicit_statement(question, scope) if not question_course else None)
        if statement and statement.scope != scope:
            raise ValueError('自述来源范围必须与本轮 scope 一致')
        request, started = self.memory.begin(session, self.settings.agent.id, question, scope, scene, statement, question_course=question_course)
        result = self.graph.invoke({'input': question, 'agent': self.settings.agent, 'session': session,
            'style_scope': scope, 'response_scene': scene, 'user_statement': statement,
            'question_course': question_course,
            'request_id': request, 'started_at': started}, self.configuration(session),
            durability='sync', interrupt_after=interrupt_after)
        if not result.get('reply'):
            raise TurnPaused(request, self.graph.get_state(self.configuration(session)).next)
        return result['reply']

    def resume(self, session='default'):
        try:
            return self._resume(session)
        except sqlite3.Error as exc:
            raise self.database_error(exc, 'resume') from exc

    def _resume(self, session='default'):
        config = self.configuration(session)
        pending = self.memory.pending(session, self.settings.agent.id)
        snapshot = self.graph.get_state(config)
        if not pending and not snapshot.values:
            raise ValueError('该会话没有可恢复的检查点')
        completed = self.memory.completed_at(snapshot.values.get('request_id'))
        if not pending and (completed or snapshot.values.get('started_at', 0)) < time.time()-self.memory.ttl:
            self.checkpoints.saver.delete_thread(config['configurable']['thread_id'])
            raise ValueError('该会话的已完成检查点已过期')
        if not pending and not snapshot.next:
            return Reply.model_validate(snapshot.values['reply'])
        initial = None
        # A process may die after the run is committed but before its first
        # graph checkpoint. The thread can still hold the previous reply.
        if pending and snapshot.values.get('request_id') != pending['id']:
            initial = {'input': pending['question'], 'agent': self.settings.agent, 'session': session,
                       'style_scope': pending['scope'], 'response_scene': pending['scene'],
                       'question_course': pending['question_course'],
                       'user_statement': UserStatement.model_validate_json(pending['statement_json']) if pending['statement_json'] else None,
                       'request_id': pending['id'], 'started_at': pending['started_at']}
        self._resuming = True
        try:
            if pending and initial is None:
                state = dict(snapshot.values)
                if state.get('context_expires_at', 0) <= time.time():
                    state['response_scene'] = None
                context = self.context(state)
                # Refresh data before a resumed intent or tool node can read it.
                # Keep the saved plan and successful tool results for replay.
                keys = ('history', 'history_source_ids', 'history_summary', 'summary_source_ids',
                        'style_examples', 'profile_evidence', 'style_details', 'profile_details',
                        'feedback_details', 'context_expires_at')
                self.graph.update_state(config, {**{key: context[key] for key in keys},
                    'response_scene': state.get('response_scene'),
                    'warnings': [*state['warnings'], *context['warnings']],
                    'errors': [*state.get('errors', []), *context['errors']], 'resume_refreshed': False})
            result = self.graph.invoke(initial, config, durability='sync')
            return Reply.model_validate(result['reply'])
        finally:
            self._resuming = False

    def close(self):
        self.memory.close()
        self.checkpoints.close()
        if self.model:
            self.model.close()
        if self.style_repository:
            self.style_repository.close()
        if self.retriever:
            self.retriever.close()
        if self.retrieval_repository:
            self.retrieval_repository.close()
