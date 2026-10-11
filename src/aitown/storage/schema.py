import sqlite3

VERSION = 4
DDL = """
CREATE TABLE import_batches(
 id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, imported_at REAL NOT NULL,
 first_timestamp INTEGER, last_timestamp INTEGER, status TEXT NOT NULL CHECK(status IN ('importing','ready')));
CREATE TABLE message_refs(
 id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
 source_id INTEGER NOT NULL, conversation_id TEXT NOT NULL, alias TEXT NOT NULL,
 timestamp INTEGER NOT NULL, direction TEXT NOT NULL, speaker_id TEXT NOT NULL,
 type INTEGER NOT NULL, status TEXT NOT NULL, content TEXT NOT NULL, review_status TEXT NOT NULL,
 UNIQUE(batch_id,source_id));
CREATE TABLE deleted_sources(
 batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE, source_id INTEGER NOT NULL,
 PRIMARY KEY(batch_id,source_id));
CREATE TABLE episodes(
 id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL, conversation_id TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
 quality TEXT NOT NULL, UNIQUE(batch_id,source_id));
CREATE TABLE episode_messages(
 episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
 message_id INTEGER NOT NULL UNIQUE REFERENCES message_refs(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, PRIMARY KEY(episode_id,message_id), UNIQUE(episode_id,ordinal));
CREATE TABLE message_links(
 message_id INTEGER PRIMARY KEY REFERENCES message_refs(id) ON DELETE CASCADE,
 target_id INTEGER REFERENCES message_refs(id) ON DELETE SET NULL, status TEXT NOT NULL);
CREATE TABLE facts(
 id INTEGER PRIMARY KEY, subject TEXT NOT NULL, attribute TEXT NOT NULL, value TEXT NOT NULL,
 valid_from INTEGER, valid_to INTEGER, status TEXT NOT NULL CHECK(status IN ('active','superseded')),
 superseded_by INTEGER REFERENCES facts(id) ON DELETE SET NULL);
CREATE TABLE fact_sources(
 fact_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
 message_id INTEGER NOT NULL REFERENCES message_refs(id) ON DELETE CASCADE, PRIMARY KEY(fact_id,message_id));
CREATE TABLE persona_traits(
 id INTEGER PRIMARY KEY, trait TEXT NOT NULL, scene TEXT NOT NULL,
 basis TEXT NOT NULL CHECK(basis IN ('direct','inferred')), status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE trait_sources(
 trait_id INTEGER NOT NULL REFERENCES persona_traits(id) ON DELETE CASCADE,
 message_id INTEGER NOT NULL REFERENCES message_refs(id) ON DELETE CASCADE, PRIMARY KEY(trait_id,message_id));
CREATE TABLE style_examples(
 id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL, episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
 scene TEXT NOT NULL, split TEXT NOT NULL CHECK(split IN ('train','dev','test')),
 context TEXT NOT NULL, target TEXT NOT NULL, quality TEXT NOT NULL, UNIQUE(batch_id,source_id));
CREATE TABLE style_sources(
 style_id INTEGER NOT NULL REFERENCES style_examples(id) ON DELETE CASCADE,
 message_id INTEGER NOT NULL REFERENCES message_refs(id) ON DELETE CASCADE,
 role TEXT NOT NULL CHECK(role IN ('context','target')), ordinal INTEGER NOT NULL,
 PRIMARY KEY(style_id,message_id), UNIQUE(style_id,role,ordinal));
CREATE TABLE knowledge_documents(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, content TEXT NOT NULL, purpose TEXT NOT NULL);
CREATE TABLE document_sources(
 document_id INTEGER NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
 message_id INTEGER NOT NULL REFERENCES message_refs(id) ON DELETE CASCADE, PRIMARY KEY(document_id,message_id));
CREATE TABLE chunks(
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, content TEXT NOT NULL, UNIQUE(document_id,ordinal));
CREATE TABLE embedding_items(
 id INTEGER PRIMARY KEY, index_kind TEXT NOT NULL CHECK(index_kind IN ('fact','trait','style','document')),
 fact_id INTEGER REFERENCES facts(id) ON DELETE CASCADE,
 trait_id INTEGER REFERENCES persona_traits(id) ON DELETE CASCADE,
 style_id INTEGER REFERENCES style_examples(id) ON DELETE CASCADE,
 chunk_id INTEGER REFERENCES chunks(id) ON DELETE CASCADE,
 vector_id INTEGER NOT NULL, model TEXT NOT NULL, dimension INTEGER NOT NULL CHECK(dimension>0), revision INTEGER NOT NULL,
 CHECK((index_kind='fact' AND fact_id IS NOT NULL AND trait_id IS NULL AND style_id IS NULL AND chunk_id IS NULL) OR
       (index_kind='trait' AND trait_id IS NOT NULL AND fact_id IS NULL AND style_id IS NULL AND chunk_id IS NULL) OR
       (index_kind='style' AND style_id IS NOT NULL AND fact_id IS NULL AND trait_id IS NULL AND chunk_id IS NULL) OR
       (index_kind='document' AND chunk_id IS NOT NULL AND fact_id IS NULL AND trait_id IS NULL AND style_id IS NULL)));
CREATE TABLE data_revisions(kind TEXT PRIMARY KEY, revision INTEGER NOT NULL);
INSERT INTO data_revisions VALUES('fact',0),('trait',0),('style',0),('document',0);
CREATE TABLE chat_sessions(id TEXT PRIMARY KEY, agent_id TEXT NOT NULL);
CREATE TABLE chat_turns(
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
 question TEXT NOT NULL, answer TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL);
CREATE TABLE feedback(
 id INTEGER PRIMARY KEY, turn_id INTEGER NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, correction TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE skill_runs(
 id INTEGER PRIMARY KEY, turn_id INTEGER NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE,
 name TEXT NOT NULL, argument_kind TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX messages_by_batch_conversation ON message_refs(batch_id,conversation_id,timestamp,source_id);
CREATE INDEX style_by_split ON style_examples(split);
CREATE INDEX chat_by_session ON chat_turns(session_id,created,id);
"""


MEMORY_DDL = """
CREATE TABLE memories(
 id INTEGER PRIMARY KEY, entry_key TEXT NOT NULL UNIQUE,
 episode_id INTEGER REFERENCES episodes(id) ON DELETE CASCADE,
 category TEXT NOT NULL CHECK(category IN ('event','preference','long_term','plan','other_person','inference','unknown')),
 subject TEXT NOT NULL, basis TEXT NOT NULL CHECK(basis IN ('direct','inferred','unknown')),
 status TEXT NOT NULL CHECK(status IN ('candidate','confirmed','unknown','rejected','superseded')),
 scope TEXT NOT NULL, split TEXT NOT NULL CHECK(split IN ('train','dev','test','unassigned')),
 occurred_at INTEGER, recorded_at INTEGER NOT NULL, valid_from INTEGER, valid_to INTEGER,
 payload TEXT NOT NULL, fact_id INTEGER REFERENCES facts(id) ON DELETE CASCADE);
CREATE TABLE memory_sources(
 memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
 message_id INTEGER NOT NULL REFERENCES message_refs(id) ON DELETE CASCADE,
 PRIMARY KEY(memory_id,message_id));
CREATE INDEX memory_by_subject_time ON memories(subject,category,valid_from,valid_to);
"""


RUNTIME_DDL = """
CREATE TABLE runtime_sessions(
 agent_id TEXT NOT NULL, session_id TEXT NOT NULL,
 thread_id TEXT NOT NULL UNIQUE REFERENCES chat_sessions(id) ON DELETE CASCADE,
 PRIMARY KEY(agent_id,session_id));
CREATE TABLE runtime_runs(
 id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES chat_sessions(id),
 question TEXT NOT NULL, scope TEXT NOT NULL, scene TEXT, statement_json TEXT, started_at REAL NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','complete')),
 reply_json TEXT, turn_id INTEGER REFERENCES chat_turns(id) ON DELETE SET NULL);
CREATE UNIQUE INDEX one_pending_run ON runtime_runs(thread_id) WHERE status='pending';
ALTER TABLE chat_turns ADD COLUMN request_id TEXT;
ALTER TABLE chat_turns ADD COLUMN important INTEGER NOT NULL DEFAULT 1;
CREATE UNIQUE INDEX chat_by_request ON chat_turns(request_id) WHERE request_id IS NOT NULL;
CREATE TABLE runtime_skill_calls(
 request_id TEXT NOT NULL REFERENCES runtime_runs(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, name TEXT NOT NULL, arguments TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(request_id,ordinal));
CREATE TABLE transient_context(
 thread_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, cache_key TEXT NOT NULL, value TEXT NOT NULL, expires_at REAL NOT NULL,
 PRIMARY KEY(thread_id,kind,cache_key));
CREATE TABLE context_summaries(
 id INTEGER PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE REFERENCES chat_sessions(id) ON DELETE CASCADE,
 content TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE summary_sources(
 summary_id INTEGER NOT NULL REFERENCES context_summaries(id) ON DELETE CASCADE,
 turn_id INTEGER NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE,
 PRIMARY KEY(summary_id,turn_id));
CREATE TABLE runtime_legacy_imports(
 source_path TEXT NOT NULL, source_id INTEGER NOT NULL,
 turn_id INTEGER REFERENCES chat_turns(id) ON DELETE SET NULL, PRIMARY KEY(source_path,source_id));
CREATE TABLE source_records(
 id INTEGER PRIMARY KEY, source_kind TEXT NOT NULL CHECK(source_kind IN ('user_statement','user_correction')),
 agent_id TEXT NOT NULL, session_id TEXT NOT NULL, scope TEXT NOT NULL,
 content TEXT NOT NULL, created REAL NOT NULL,
 request_id TEXT UNIQUE REFERENCES runtime_runs(id) ON DELETE SET NULL);
CREATE TABLE fact_statement_sources(
 fact_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
 record_id INTEGER NOT NULL REFERENCES source_records(id) ON DELETE CASCADE,
 PRIMARY KEY(fact_id,record_id));
"""


def migrate(conn):
    conn.execute("PRAGMA foreign_keys=ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > VERSION:
        raise ValueError("Application database schema is newer than this program")
    if version == 0:
        try:
            conn.executescript("BEGIN;\n"+DDL+"\nPRAGMA user_version=1;\nCOMMIT;")
        except sqlite3.Error:
            conn.rollback()
            raise
        version = 1
    if version == 1:
        try:
            conn.executescript("BEGIN;\n"+MEMORY_DDL+"\nPRAGMA user_version=2;\nCOMMIT;")
        except sqlite3.Error:
            conn.rollback()
            raise
        version = 2
    if version == 2:
        try:
            conn.executescript("BEGIN;\n"+RUNTIME_DDL+"\nPRAGMA user_version=3;\nCOMMIT;")
        except sqlite3.Error:
            conn.rollback()
            raise
        version = 3
    if version == 3:
        try:
            conn.executescript("BEGIN;\nALTER TABLE runtime_runs ADD COLUMN question_course TEXT NOT NULL DEFAULT '';\nPRAGMA user_version=4;\nCOMMIT;")
        except sqlite3.Error:
            conn.rollback()
            raise
    with conn:
        conn.execute("INSERT OR IGNORE INTO data_revisions VALUES('memory',0)")
