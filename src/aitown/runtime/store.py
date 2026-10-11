import json
import sqlite3
import time
import uuid
from pathlib import Path

from ..models import Reply, SkillResult
from .models import UserStatement
from .summary import encode, important, summarize


class RuntimeStore:
    def __init__(self, repository, turns=8, ttl_hours=168, transient_seconds=600):
        if turns < 5:
            raise ValueError('History must retain at least five turns')
        self.repo, self.conn = repository, repository.conn
        self.turns, self.ttl = turns, ttl_hours*3600
        self.transient_seconds = transient_seconds

    @staticmethod
    def thread_id(session, agent_id):
        return json.dumps([agent_id, session], ensure_ascii=False, separators=(',', ':'))

    def session(self, session, agent_id):
        thread = self.thread_id(session, agent_id)
        with self.conn:
            self.conn.execute('INSERT OR IGNORE INTO chat_sessions VALUES(?,?)', (thread, agent_id))
            owner = self.conn.execute('SELECT agent_id FROM chat_sessions WHERE id=?', (thread,)).fetchone()[0]
            if owner != agent_id:
                raise ValueError('Session thread belongs to a different agent')
            self.conn.execute('INSERT OR IGNORE INTO runtime_sessions VALUES(?,?,?)', (agent_id, session, thread))
        return thread

    def import_legacy(self, path):
        path = Path(path)
        if not path.is_file():
            return 0
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as legacy:
            if not legacy.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='turns'").fetchone():
                return 0
            columns = {row[1] for row in legacy.execute('PRAGMA table_info(turns)')}
            agent_column = 'agent_id' if 'agent_id' in columns else 'resident'
            rows = legacy.execute(f'SELECT id,session,{agent_column},question,answer,created FROM turns ORDER BY id').fetchall()
        added = 0
        for old_id, session, agent, question, answer, created in rows:
            thread = self.session(session, agent)
            with self.conn:
                self.conn.execute('BEGIN IMMEDIATE')
                if self.conn.execute('SELECT 1 FROM runtime_legacy_imports WHERE source_path=? AND source_id=?', (str(path.resolve()), old_id)).fetchone():
                    continue
                if created >= time.time()-self.ttl:
                    turn_id = self.conn.execute("INSERT INTO chat_turns(session_id,question,answer,created,status,important) VALUES(?,?,?,?,'legacy_output',1)",
                                                (thread, question, answer, created)).lastrowid
                    added += 1
                else:
                    turn_id = None
                self.conn.execute('INSERT INTO runtime_legacy_imports VALUES(?,?,?)', (str(path.resolve()), old_id, turn_id))
        return added

    def cleanup(self, now=None):
        now = time.time() if now is None else now
        with self.conn:
            cached = self.conn.execute('DELETE FROM transient_context WHERE expires_at<=?', (now,)).rowcount
            # Pending calls keep their transaction records until recovery completes.
            self.conn.execute("DELETE FROM runtime_runs WHERE status='complete' AND COALESCE((SELECT created FROM chat_turns WHERE id=runtime_runs.turn_id),started_at)<?", (now-self.ttl,))
            self.conn.execute('DELETE FROM context_summaries WHERE id IN (SELECT s.summary_id FROM summary_sources s JOIN chat_turns t ON t.id=s.turn_id WHERE t.created<?)', (now-self.ttl,))
            history = self.conn.execute('DELETE FROM chat_turns WHERE created<?', (now-self.ttl,)).rowcount
            self.conn.execute('DELETE FROM context_summaries WHERE NOT EXISTS(SELECT 1 FROM summary_sources WHERE summary_id=context_summaries.id)')
        return {'transient_deleted': cached, 'history_deleted': history}

    def rows(self, session, agent_id, *, recent=True):
        thread = self.thread_id(session, agent_id)
        sql = 'SELECT t.* FROM chat_turns t JOIN chat_sessions s ON s.id=t.session_id WHERE t.session_id=? AND s.agent_id=? AND t.created>=? ORDER BY t.id DESC'
        parameters = [thread, agent_id, time.time()-self.ttl]
        if recent:
            sql += ' LIMIT ?'
            parameters.append(self.turns)
        return list(reversed(self.conn.execute(sql, parameters).fetchall()))

    def load(self, session, agent_id):
        self.cleanup()
        return [message for row in self.rows(session, agent_id) for message in
                ({'role': 'user', 'content': row['question']}, {'role': 'assistant', 'content': row['answer']})]

    def history_bundle(self, session, agent_id):
        history = self.load(session, agent_id)
        recent = self.rows(session, agent_id)
        cutoff = recent[0]['id'] if recent else None
        older = [r for r in self.rows(session, agent_id, recent=False) if cutoff and r['id'] < cutoff][-32:]
        summary = summarize(older)
        thread = self.session(session, agent_id)
        with self.conn:
            old = self.conn.execute('SELECT * FROM context_summaries WHERE thread_id=?', (thread,)).fetchone()
            content = encode(summary)
            if old is None or old['content'] != content:
                self.conn.execute('INSERT INTO context_summaries(thread_id,content,created) VALUES(?,?,?) ON CONFLICT(thread_id) DO UPDATE SET content=excluded.content,created=excluded.created',
                                  (thread, content, time.time()))
                summary_id = self.conn.execute('SELECT id FROM context_summaries WHERE thread_id=?', (thread,)).fetchone()[0]
                self.conn.execute('DELETE FROM summary_sources WHERE summary_id=?', (summary_id,))
                self.conn.executemany('INSERT INTO summary_sources VALUES(?,?)', [(summary_id, i) for i in summary['source_ids']])
        return {'history': history, 'history_source_ids': [r['id'] for r in recent], 'history_summary': summary,
                'summary_source_ids': summary['source_ids']}

    def source_turn(self, turn_id, session, agent_id):
        row = self.conn.execute('SELECT t.* FROM chat_turns t JOIN chat_sessions s ON s.id=t.session_id WHERE t.id=? AND t.session_id=? AND s.agent_id=? AND t.created>=?',
                                (turn_id, self.thread_id(session, agent_id), agent_id, time.time()-self.ttl)).fetchone()
        return dict(row) if row else None

    def save(self, session, agent_id, question, answer):
        thread = self.session(session, agent_id)
        return self.repo.save_history(thread, agent_id, question, answer)

    def pending(self, session, agent_id):
        row = self.conn.execute("SELECT * FROM runtime_runs WHERE thread_id=? AND status='pending'", (self.thread_id(session, agent_id),)).fetchone()
        return dict(row) if row else None

    def begin(self, session, agent_id, question, scope, scene, statement=None, *, question_course=''):
        thread = self.session(session, agent_id)
        request_id, started = str(uuid.uuid4()), time.time()
        with self.conn:
            self.conn.execute("INSERT INTO runtime_runs(id,thread_id,question,scope,scene,statement_json,started_at,question_course,status) VALUES(?,?,?,?,?,?,?,?,'pending')",
                              (request_id, thread, question, scope, scene, statement.model_dump_json() if statement else None, started, question_course))
        return request_id, started

    def finished(self, request_id):
        row = self.conn.execute("SELECT reply_json FROM runtime_runs WHERE id=? AND status='complete'", (request_id,)).fetchone()
        return Reply.model_validate_json(row[0]) if row else None

    def completed_at(self, request_id):
        row = self.conn.execute("SELECT t.created FROM runtime_runs r JOIN chat_turns t ON t.id=r.turn_id WHERE r.id=? AND r.status='complete'", (request_id,)).fetchone()
        return row[0] if row else None

    def complete(self, request_id, reply, agent_id, mode):
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            run = self.conn.execute('SELECT * FROM runtime_runs WHERE id=?', (request_id,)).fetchone()
            if run['status'] == 'complete':
                return Reply.model_validate_json(run['reply_json'])
            reply.request_id = request_id
            turn = self.conn.execute('INSERT INTO chat_turns(session_id,question,answer,created,status,request_id,important) VALUES(?,?,?,?,?,?,?)',
                (run['thread_id'], run['question'], reply.answer, time.time(), 'model_output' if mode == 'live' else 'demo_output',
                 request_id, int(important(run['question'], reply)))).lastrowid
            for result in reply.skills:
                self.repo.record_skill(turn, result.name, 'structured', 'ok' if result.ok else 'failed', commit=False)
            self.conn.execute("UPDATE runtime_runs SET status='complete',reply_json=?,turn_id=? WHERE id=?",
                              (reply.model_dump_json(), turn, request_id))
        return reply

    def get_transient(self, session, agent_id, kind, key):
        row = self.conn.execute('SELECT value FROM transient_context WHERE thread_id=? AND kind=? AND cache_key=? AND expires_at>?',
            (self.thread_id(session, agent_id), kind, key, time.time())).fetchone()
        return json.loads(row[0]) if row else None

    def put_transient(self, session, agent_id, kind, key, value, seconds=None):
        thread = self.session(session, agent_id)
        with self.conn:
            self.conn.execute('INSERT INTO transient_context VALUES(?,?,?,?,?) ON CONFLICT(thread_id,kind,cache_key) DO UPDATE SET value=excluded.value,expires_at=excluded.expires_at',
                (thread, kind, key, json.dumps(value, ensure_ascii=False), time.time()+(self.transient_seconds if seconds is None else seconds)))

    def record_statement(self, statement, content, session, agent_id, request_id=None):
        statement = UserStatement.model_validate(statement)
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            return self._record_statement(statement, content, session, agent_id, request_id)

    def _record_statement(self, statement, content, session, agent_id, request_id=None):
        """Called inside the caller's transaction, including transactional skills."""
        if request_id:
            old = self.conn.execute('SELECT id FROM source_records WHERE request_id=?', (request_id,)).fetchone()
            if old:
                return self.conn.execute('SELECT fact_id FROM fact_statement_sources WHERE record_id=?', (old['id'],)).fetchone()[0]
        previous = None
        if statement.corrects_fact_id is not None:
            previous = self.conn.execute("SELECT * FROM facts WHERE id=? AND subject='self' AND status='active'", (statement.corrects_fact_id,)).fetchone()
            if previous is None or statement.scope not in {s.conversation_id for s in self.repo.sources('fact', previous['id'])}:
                raise ValueError('Correction target must be active and within the declared self scope')
            if previous['attribute'] != statement.attribute:
                raise ValueError('Correction must retain the original attribute')
        now = int(time.time())
        record = self.conn.execute('INSERT INTO source_records(source_kind,agent_id,session_id,scope,content,created,request_id) VALUES(?,?,?,?,?,?,?)',
                                  (statement.kind, agent_id, session, statement.scope, content, now, request_id)).lastrowid
        fact = self.conn.execute("INSERT INTO facts(subject,attribute,value,valid_from,status) VALUES('self',?,?,?,'active')",
                                 (statement.attribute, statement.value, now)).lastrowid
        self.conn.execute('INSERT INTO fact_statement_sources VALUES(?,?)', (fact, record))
        if previous:
            self.conn.execute("UPDATE facts SET status='superseded',superseded_by=?,valid_to=? WHERE id=?", (fact, now, previous['id']))
            self.conn.execute("UPDATE memories SET status='superseded',valid_to=? WHERE fact_id=?", (now, previous['id']))
            self.repo._bump('memory')
        self.repo._bump('fact')
        return fact

    def execute_skill(self, registry, call, request_id, ordinal, *, repository=None):
        from ..skills import SkillContext
        run = self.conn.execute('SELECT r.*,s.session_id,s.agent_id FROM runtime_runs r JOIN runtime_sessions s ON s.thread_id=r.thread_id WHERE r.id=?', (request_id,)).fetchone()
        context = SkillContext(repository or self.repo, scope=run['scope'], question=run['question'],
                               session=run['session_id'], agent_id=run['agent_id'], runtime=self)
        arguments = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
        committed = self.conn.execute('SELECT * FROM runtime_skill_calls WHERE request_id=? AND ordinal=?', (request_id, ordinal)).fetchone()
        if committed:
            if committed['name'] != call.name or committed['arguments'] != arguments:
                return SkillResult(name=call.name, ok=False, error='Recorded write call has different arguments')
            return SkillResult.model_validate_json(committed['result'])
        skill = registry._skills.get(call.name)
        if skill is None or call.name not in registry._enabled or skill.read_only:
            return registry.execute(call, context)
        try:
            with self.conn:
                self.conn.execute('BEGIN IMMEDIATE')
                old = self.conn.execute('SELECT * FROM runtime_skill_calls WHERE request_id=? AND ordinal=?', (request_id, ordinal)).fetchone()
                if old:
                    if old['name'] != call.name or old['arguments'] != arguments:
                        raise ValueError('Recorded write call has different arguments')
                    return SkillResult.model_validate_json(old['result'])
                parsed = skill.arguments.model_validate(call.arguments)
                # Business writes and the successful result use one connection and
                # transaction. External writes require their own idempotency API.
                result = SkillResult(name=call.name, ok=True,
                                     data=skill.result(skill.run_transaction_with_context(parsed, self.conn, context)))
                self.conn.execute('INSERT INTO runtime_skill_calls VALUES(?,?,?,?,?)',
                                  (request_id, ordinal, call.name, arguments, result.model_dump_json()))
            return result
        except Exception as exc:
            return SkillResult(name=call.name, ok=False, error=str(exc))

    def close(self):
        self.repo.close()
