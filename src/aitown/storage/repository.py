import json
import sqlite3
import time
from pathlib import Path

from .models import Fact, HistoryTurn, Source, Style, Trait
from .schema import migrate

KINDS = {"fact":("facts","fact_sources","fact_id"),
         "trait":("persona_traits","trait_sources","trait_id"),
         "style":("style_examples","style_sources","style_id"),
         "document":("knowledge_documents","document_sources","document_id"),
         "memory":("memories","memory_sources","memory_id")}


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Repository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        migrate(self.conn)

    def close(self):
        self.conn.close()

    def _bump(self, kind):
        self.conn.execute("UPDATE data_revisions SET revision=revision+1 WHERE kind=?",(kind,))
        self.conn.execute("DELETE FROM embedding_items WHERE index_kind=?",(kind,))

    def revision(self, kind):
        row = self.conn.execute("SELECT revision FROM data_revisions WHERE kind=?",(kind,)).fetchone()
        if row is None:
            raise ValueError("Unknown index kind")
        return row[0]

    def counts(self):
        names = ("import_batches","message_refs","episodes","episode_messages","style_examples",
                 "facts","persona_traits","knowledge_documents","chunks","embedding_items","chat_turns","memories")
        return {n:self.conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in names}

    def _invalidate_sources(self, ids):
        for mid in ids:
            for kind,(table,association,column) in KINDS.items():
                affected = [r[0] for r in self.conn.execute(f"SELECT {column} FROM {association} WHERE message_id=?",(mid,))]
                for entity in affected:
                    self.conn.execute(f"DELETE FROM {table} WHERE id=?",(entity,))
                if affected:
                    self._bump(kind)

    def import_t3(self, directory: Path, batch_id: str | None = None):
        directory = Path(directory)
        messages = read_lines(directory/"normalized_messages.jsonl")
        episodes = read_lines(directory/"episodes.jsonl")
        examples = [r for split in ("train","dev","test") for r in read_lines(directory/(split+".jsonl"))]
        reviews = json.loads((directory/"message_reviews.json").read_text(encoding="utf-8"))
        references = json.loads((directory/"references.json").read_text(encoding="utf-8"))
        if not messages:
            raise ValueError("T3 import contains no messages")
        batch_id = batch_id or messages[0]["batch_id"]
        if any(m["batch_id"] != messages[0]["batch_id"] for m in messages):
            raise ValueError("Import one source batch at a time")
        before = self.counts()
        # All source, event, sample and index mutations roll back together on failure.
        with self.conn:
            self.conn.execute("INSERT INTO import_batches VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status='importing'",
                (batch_id,"cleaned_wechat",time.time(),min(m["timestamp"] for m in messages),max(m["timestamp"] for m in messages),"importing"))
            deleted = {r[0] for r in self.conn.execute("SELECT source_id FROM deleted_sources WHERE batch_id=?",(batch_id,))}
            mid_map = {}
            for m in messages:
                if m["id"] in deleted:
                    continue
                values = (m["conversation_id"],m["conversation_id"],m["timestamp"],m["direction"],m["speaker_id"],
                          m["type"],m["status"],m["content"],reviews.get(str(m["id"]),"pending"))
                old = self.conn.execute("SELECT * FROM message_refs WHERE batch_id=? AND source_id=?",(batch_id,m["id"])).fetchone()
                keys = ("conversation_id","alias","timestamp","direction","speaker_id","type","status","content","review_status")
                if old:
                    if tuple(old[k] for k in keys) != values:
                        self._invalidate_sources([old["id"]])
                        self.conn.execute("UPDATE message_refs SET conversation_id=?,alias=?,timestamp=?,direction=?,speaker_id=?,type=?,status=?,content=?,review_status=? WHERE id=?",(*values,old["id"]))
                    mid_map[m["id"]] = old["id"]
                else:
                    cursor = self.conn.execute("INSERT INTO message_refs(batch_id,source_id,conversation_id,alias,timestamp,direction,speaker_id,type,status,content,review_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(batch_id,m["id"],*values))
                    mid_map[m["id"]] = cursor.lastrowid
            eid_map = {}
            # Re-segmentation may move a message to a different event in the same batch.
            self.conn.execute("DELETE FROM episode_messages WHERE episode_id IN (SELECT id FROM episodes WHERE batch_id=?)",(batch_id,))
            desired_episodes = {e["episode_id"] for e in episodes}
            for old in self.conn.execute("SELECT id,source_id FROM episodes WHERE batch_id=?",(batch_id,)).fetchall():
                if old["source_id"] not in desired_episodes:
                    self.conn.execute("DELETE FROM episodes WHERE id=?",(old["id"],))
                    self._bump("style")
            for e in episodes:
                ids = [mid_map[i] for i in e["message_ids"] if i not in deleted]
                if not ids:
                    continue
                if any(m["conversation_id"] != e["conversation_id"] for m in messages if m["id"] in e["message_ids"]):
                    raise ValueError("Episode sources belong to another conversation")
                self.conn.execute("INSERT INTO episodes(batch_id,source_id,conversation_id,start,end,quality) VALUES(?,?,?,?,?,?) ON CONFLICT(batch_id,source_id) DO UPDATE SET start=excluded.start,end=excluded.end,quality=excluded.quality",
                    (batch_id,e["episode_id"],e["conversation_id"],e["start"],e["end"],json.dumps(e["boundary_reasons"])))
                eid = self.conn.execute("SELECT id FROM episodes WHERE batch_id=? AND source_id=?",(batch_id,e["episode_id"])).fetchone()[0]
                eid_map[e["episode_id"]] = eid
                self.conn.execute("DELETE FROM episode_messages WHERE episode_id=?",(eid,))
                for ordinal,mid in enumerate(ids):
                    self.conn.execute("INSERT INTO episode_messages VALUES(?,?,?)",(eid,mid,ordinal))
            for key,ref in references.items():
                if int(key) not in mid_map:
                    continue
                target = mid_map.get(ref.get("target_id"))
                status = ref["status"] if target or ref["status"] != "resolved" else "missing"
                self.conn.execute("INSERT INTO message_links VALUES(?,?,?) ON CONFLICT(message_id) DO UPDATE SET target_id=excluded.target_id,status=excluded.status",(mid_map[int(key)],target,status))
            desired_examples = {e["example_id"] for e in examples}
            for old in self.conn.execute("SELECT id,source_id FROM style_examples WHERE batch_id=?",(batch_id,)).fetchall():
                if old["source_id"] not in desired_examples:
                    self.conn.execute("DELETE FROM style_examples WHERE id=?",(old["id"],))
                    self._bump("style")
            for e in examples:
                sources = [*e["context_message_ids"],*e["target_message_ids"]]
                if any(i in deleted for i in sources):
                    continue
                ids = [mid_map[i] for i in sources]  # A missing source aborts the transaction.
                if any(reviews.get(str(i)) != "approved" for i in sources):
                    raise ValueError("Style sample uses unreviewed source")
                targets = [m for m in messages if m["id"] in e["target_message_ids"]]
                if not targets or any(m["direction"] != "outgoing" or m["speaker_id"] != "self" for m in targets):
                    raise ValueError("Style target is not exclusively self speech")
                context_rows = [m for m in messages if m["id"] in e["context_message_ids"]]
                if any(m["type"] != 1 or m["status"] != "text" or not m["eligible_text"] for m in [*targets,*context_rows]):
                    raise ValueError("Style sources must be readable ordinary text")
                earliest = min((m["timestamp"],m["id"]) for m in targets)
                if any((m["timestamp"],m["id"]) >= earliest for m in context_rows):
                    raise ValueError("Style context includes future or target content")
                if any(m["conversation_id"] != e["conversation_id"] for m in [*targets,*context_rows]):
                    raise ValueError("Style sources cross conversations")
                # Derive text from authoritative messages, not an unverified copied target.
                ordered_targets = [next(m for m in targets if m["id"]==i)["content"] for i in e["target_message_ids"]]
                if e["target"] != "\n".join(ordered_targets):
                    raise ValueError("Style target differs from its sources")
                for part in e["messages"]:
                    if part["content"] != "\n".join(next(m for m in context_rows if m["id"]==i)["content"] for i in part["source_ids"]):
                        raise ValueError("Style context differs from its sources")
                    expected = {"incoming":"user","outgoing":"assistant"}
                    if any(expected.get(next(m for m in context_rows if m["id"]==i)["direction"]) != part["role"] for i in part["source_ids"]):
                        raise ValueError("Style context role differs from speaker direction")
                if [i for part in e["messages"] for i in part["source_ids"]] != e["context_message_ids"]:
                    raise ValueError("Style context source order differs")
                eid = eid_map[e["episode_id"]]
                if any(self.conn.execute("SELECT episode_id FROM episode_messages WHERE message_id=?",(mid_map[i],)).fetchone()[0] != eid for i in e["target_message_ids"]):
                    raise ValueError("Style target belongs to another episode")
                values = (eid_map[e["episode_id"]],e["scene"],e["split"],json.dumps(e["messages"],ensure_ascii=False),e["target"],json.dumps(e["quality_labels"]))
                old = self.conn.execute("SELECT * FROM style_examples WHERE batch_id=? AND source_id=?",(batch_id,e["example_id"])).fetchone()
                keys = ("episode_id","scene","split","context","target","quality")
                if old and tuple(old[k] for k in keys)==values:
                    continue
                self.conn.execute("INSERT INTO style_examples(batch_id,source_id,episode_id,scene,split,context,target,quality) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(batch_id,source_id) DO UPDATE SET episode_id=excluded.episode_id,scene=excluded.scene,split=excluded.split,context=excluded.context,target=excluded.target,quality=excluded.quality",(batch_id,e["example_id"],*values))
                sid = self.conn.execute("SELECT id FROM style_examples WHERE batch_id=? AND source_id=?",(batch_id,e["example_id"])).fetchone()[0]
                self.conn.execute("DELETE FROM style_sources WHERE style_id=?",(sid,))
                for role,key in (("context","context_message_ids"),("target","target_message_ids")):
                    for ordinal,source in enumerate(e[key]):
                        self.conn.execute("INSERT INTO style_sources VALUES(?,?,?,?)",(sid,mid_map[source],role,ordinal))
                self._bump("style")
            self.conn.execute("UPDATE import_batches SET status='ready',first_timestamp=?,last_timestamp=? WHERE id=?",
                (min(m["timestamp"] for m in messages),max(m["timestamp"] for m in messages),batch_id))
        after = self.counts()
        return {"batch_id":batch_id,"counts":after,"added":{k:after[k]-before[k] for k in after},"deleted_sources_skipped":len(deleted)}

    def sources(self, kind, entity_id):
        if kind not in KINDS:
            raise ValueError("Unknown source entity kind")
        _,association,column = KINDS[kind]
        result = self.conn.execute(f"SELECT m.id AS message_id,m.batch_id,m.source_id,m.conversation_id,m.alias,m.timestamp,m.direction,m.speaker_id,m.status,m.content,em.episode_id FROM {association} s JOIN message_refs m ON m.id=s.message_id LEFT JOIN episode_messages em ON em.message_id=m.id WHERE s.{column}=? ORDER BY m.timestamp,m.source_id",(entity_id,)).fetchall()
        sources = [Source(**dict(row)) for row in result]
        if kind == 'fact':
            records = self.conn.execute("SELECT r.* FROM source_records r JOIN fact_statement_sources s ON s.record_id=r.id WHERE s.fact_id=? ORDER BY r.created,r.id", (entity_id,))
            sources.extend(Source(source_kind=row['source_kind'], record_id=row['id'], conversation_id=row['scope'],
                alias=row['scope'], timestamp=int(row['created']), direction='outgoing', speaker_id='self',
                status='explicit_user', content=row['content']) for row in records)
        return sources

    def _source_ids(self, ids):
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise ValueError("At least one source is required")
        for mid in ids:
            row = self.conn.execute("SELECT review_status FROM message_refs WHERE id=?",(mid,)).fetchone()
            if row is None or row[0] != "approved":
                raise ValueError("Source must exist and be reviewed")
        return ids

    def _add_fact(self, subject, attribute, value, source_ids, valid_from=None, valid_to=None):
        ids = self._source_ids(source_ids)
        cursor = self.conn.execute("INSERT INTO facts(subject,attribute,value,valid_from,valid_to,status) VALUES(?,?,?,?,?,'active')",(subject,attribute,value,valid_from,valid_to))
        self.conn.executemany("INSERT INTO fact_sources VALUES(?,?)",[(cursor.lastrowid,i) for i in ids])
        self._bump("fact")
        return cursor.lastrowid

    def add_fact(self, subject, attribute, value, source_ids, valid_from=None, valid_to=None):
        with self.conn:
            return self._add_fact(subject,attribute,value,source_ids,valid_from,valid_to)

    def correct_fact(self, fact_id, value, source_ids):
        with self.conn:
            old = self.conn.execute("SELECT * FROM facts WHERE id=? AND status='active'",(fact_id,)).fetchone()
            if old is None:
                raise ValueError("Active fact not found")
            new_id = self._add_fact(old["subject"],old["attribute"],value,source_ids,old["valid_from"],old["valid_to"])
            self.conn.execute("UPDATE facts SET status='superseded',superseded_by=? WHERE id=?",(new_id,fact_id))
            self.conn.execute("UPDATE memories SET status='superseded' WHERE fact_id=?",(fact_id,))
            return new_id

    def add_trait(self, trait, scene, basis, source_ids):
        with self.conn:
            ids = self._source_ids(source_ids)
            cursor = self.conn.execute("INSERT INTO persona_traits(trait,scene,basis) VALUES(?,?,?)",(trait,scene,basis))
            self.conn.executemany("INSERT INTO trait_sources VALUES(?,?)",[(cursor.lastrowid,i) for i in ids])
            self._bump("trait")
            return cursor.lastrowid

    def query_facts(self, keyword, limit=20, subject=None):
        result = self.conn.execute("SELECT * FROM facts WHERE status='active' AND (? IS NULL OR subject=?) AND (instr(value,?)>0 OR instr(attribute,?)>0) ORDER BY id LIMIT ?",(subject,subject,keyword,keyword,limit))
        return [Fact(**dict(row)) for row in result]

    def query_styles(self, keyword, limit=20, split="train"):
        result = self.conn.execute("SELECT * FROM style_examples WHERE split=? AND (instr(target,?)>0 OR instr(context,?)>0) ORDER BY id LIMIT ?",(split,keyword,keyword,limit))
        return [Style(**dict(row)) for row in result]

    def query_traits(self, keyword, limit=20, basis="direct"):
        return [Trait(**dict(row)) for row in self.conn.execute("SELECT * FROM persona_traits WHERE status='active' AND basis=? AND instr(trait,?)>0 ORDER BY id LIMIT ?",(basis,keyword,limit))]

    def save_history(self, session, agent_id, question, answer):
        with self.conn:
            self.conn.execute("INSERT INTO chat_sessions VALUES(?,?) ON CONFLICT(id) DO NOTHING",(session,agent_id))
            owner = self.conn.execute("SELECT agent_id FROM chat_sessions WHERE id=?",(session,)).fetchone()[0]
            if owner != agent_id:
                raise ValueError("Session belongs to another Agent")
            cursor = self.conn.execute("INSERT INTO chat_turns(session_id,question,answer,created,status) VALUES(?,?,?,?,'complete')",(session,question,answer,time.time()))
            return cursor.lastrowid

    def query_history(self, session, keyword="", limit=16, agent_id="anotherme"):
        mapping = self.conn.execute("SELECT thread_id FROM runtime_sessions WHERE agent_id=? AND session_id=?", (agent_id,session)).fetchone()
        if mapping:
            session = mapping[0]
        return [HistoryTurn(**dict(row)) for row in self.conn.execute("SELECT t.* FROM chat_turns t JOIN chat_sessions s ON s.id=t.session_id WHERE session_id=? AND s.agent_id=? AND (instr(question,?)>0 OR instr(answer,?)>0) ORDER BY t.id DESC LIMIT ?",(session,agent_id,keyword,keyword,limit))]

    def add_feedback(self, turn_id, kind, correction=""):
        with self.conn:
            return self.conn.execute("INSERT INTO feedback(turn_id,kind,correction,created) VALUES(?,?,?,?)",(turn_id,kind,correction,time.time())).lastrowid

    def record_skill(self, turn_id, name, argument_kind, status, *, commit=True):
        if commit:
            with self.conn:
                return self.record_skill(turn_id, name, argument_kind, status, commit=False)
        return self.conn.execute("INSERT INTO skill_runs(turn_id,name,argument_kind,status,created) VALUES(?,?,?,?,?)",(turn_id,name,argument_kind,status,time.time())).lastrowid

    def add_document(self, name, content, purpose="knowledge", source_ids=()):
        from ..retrieval.chunker import document_chunks
        with self.conn:
            ids = self._source_ids(source_ids) if source_ids else []
            cursor = self.conn.execute("INSERT INTO knowledge_documents(name,content,purpose) VALUES(?,?,?)",(name,content,purpose))
            did = cursor.lastrowid
            self.conn.executemany("INSERT INTO document_sources VALUES(?,?)",[(did,i) for i in ids])
            for ordinal, piece in enumerate(document_chunks(content)):
                self.conn.execute("INSERT INTO chunks(document_id,ordinal,content) VALUES(?,?,?)",(did,ordinal,piece))
            self._bump("document")
            return did

    def upsert_document(self, name, content, purpose="knowledge"):
        """Import/update a standalone TXT/MD; unchanged content retains its ID."""
        from ..retrieval.chunker import document_chunks
        with self.conn:
            old = self.conn.execute("SELECT * FROM knowledge_documents WHERE name=?", (name,)).fetchone()
            if old is None:
                return self.add_document(name, content, purpose)
            if old['content'] == content and old['purpose'] == purpose:
                return old['id']
            if self.conn.execute("SELECT 1 FROM document_sources WHERE document_id=?", (old['id'],)).fetchone():
                raise ValueError("Managed memory documents must be updated through their source exporter")
            self.conn.execute("UPDATE knowledge_documents SET content=?,purpose=? WHERE id=?", (content, purpose, old['id']))
            self.conn.execute("DELETE FROM chunks WHERE document_id=?", (old['id'],))
            self.conn.executemany("INSERT INTO chunks(document_id,ordinal,content) VALUES(?,?,?)",
                                  [(old['id'], i, text) for i, text in enumerate(document_chunks(content))])
            self._bump("document")
            return old['id']

    def delete_document(self, document_id):
        with self.conn:
            self.conn.execute("DELETE FROM knowledge_documents WHERE id=?",(document_id,))
            self._bump("document")

    def delete_source(self, message_id):
        with self.conn:
            row = self.conn.execute("SELECT batch_id,source_id FROM message_refs WHERE id=?",(message_id,)).fetchone()
            if row is None:
                return False
            self._invalidate_sources([message_id])
            self.conn.execute("INSERT OR IGNORE INTO deleted_sources VALUES(?,?)",tuple(row))
            self.conn.execute("UPDATE message_links SET status='missing_deleted_source' WHERE target_id=?",(message_id,))
            self.conn.execute("DELETE FROM message_refs WHERE id=?",(message_id,))
            self.conn.execute("DELETE FROM episodes WHERE NOT EXISTS(SELECT 1 FROM episode_messages WHERE episode_id=episodes.id)")
            return True

    def index_items(self, kind):
        sql = {"fact":"SELECT id, value AS text FROM facts WHERE status='active'",
               "trait":"SELECT id,trait AS text FROM persona_traits WHERE status='active' AND basis='direct'",
               "style":"SELECT id,target AS text FROM style_examples WHERE split='train'",
               "document":"SELECT id,content AS text FROM chunks"}
        if kind not in sql:
            raise ValueError("Unknown index kind")
        return [dict(row) for row in self.conn.execute(sql[kind]+" ORDER BY id")]
