import json
import time
from pathlib import Path

from .models import Memory


class MemoryStore:
    def __init__(self, repository):
        self.repo = repository
        self.conn = repository.conn

    def save(self, entries, reviews=None):
        """Review records retain the interpreted fields, so stale approvals fail.

        This is dataset review, not authorization to send private data anywhere.
        All writes, preference updates and fact projections form one transaction.
        """
        reviews = reviews or {}
        added = 0
        with self.conn:
            for draft in entries:
                memory = draft.model_copy(deep=True)
                ids = self.repo._source_ids([e.message_id for e in memory.evidence])
                if memory.subject == "self" and any(e.direction != "outgoing" or e.speaker_id != "self" for e in memory.evidence):
                    raise ValueError("Other speaker evidence cannot establish self memory")
                for e in memory.evidence:
                    row = self.conn.execute("SELECT * FROM message_refs WHERE id=?", (e.message_id,)).fetchone()
                    if tuple(row[k] for k in ("batch_id", "source_id", "timestamp", "direction", "speaker_id", "content")) != (e.batch_id, e.source_id, e.timestamp, e.direction, e.speaker_id, e.excerpt):
                        raise ValueError("Evidence changed since extraction")
                    if memory.episode_id is not None and not self.conn.execute("SELECT 1 FROM episode_messages WHERE episode_id=? AND message_id=?", (memory.episode_id, e.message_id)).fetchone():
                        raise ValueError("Evidence belongs to another episode")
                decision = reviews.get(memory.entry_key)
                if decision:
                    for field in ("category", "subject", "attribute", "value", "summary"):
                        if decision[field] != getattr(memory, field):
                            raise ValueError("Review describes a different interpretation")
                    if decision["status"] not in ("confirmed", "rejected"):
                        raise ValueError("Invalid review decision")
                    memory.status = decision["status"]
                    memory = Memory.model_validate(memory.model_dump())
                elif memory.status == "confirmed":
                    raise ValueError("Confirmation requires a review record")
                payload = memory.model_dump_json()
                old = self.conn.execute("SELECT * FROM memories WHERE entry_key=?", (memory.entry_key,)).fetchone()
                if old and old["payload"] == payload:
                    continue
                if old:
                    if old["fact_id"]:
                        self.conn.execute("DELETE FROM facts WHERE id=?", (old["fact_id"],))
                        self.repo._bump("fact")
                    self.conn.execute("DELETE FROM memories WHERE entry_key=?", (memory.entry_key,))
                fact_id = None
                if memory.status == "confirmed" and memory.subject == "self" and memory.split == "train" and memory.category in ("preference", "long_term"):
                    fact_id = self.repo._add_fact(memory.subject, memory.attribute, memory.value, ids, memory.valid_from, memory.valid_to)
                cursor = self.conn.execute("INSERT INTO memories(entry_key,episode_id,category,subject,basis,status,scope,split,occurred_at,recorded_at,valid_from,valid_to,payload,fact_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                           (memory.entry_key, memory.episode_id, memory.category, memory.subject, memory.basis, memory.status, memory.scope, memory.split, memory.occurred_at, memory.recorded_at, memory.valid_from, memory.valid_to, payload, fact_id))
                self.conn.executemany("INSERT INTO memory_sources VALUES(?,?)", [(cursor.lastrowid, i) for i in ids])
                added += 1
            if added:
                self.repo._bump("memory")
            self._preference_history()
        return added

    def _preference_history(self):
        rows = self.conn.execute("SELECT m.* FROM memories m LEFT JOIN facts f ON f.id=m.fact_id WHERE m.category='preference' AND m.status IN ('confirmed','superseded') AND (m.fact_id IS NULL OR f.status='active' OR f.superseded_by IN (SELECT fact_id FROM memories WHERE fact_id IS NOT NULL)) ORDER BY m.recorded_at,m.id").fetchall()
        groups = {}
        for row in rows:
            m = Memory.model_validate_json(row["payload"])
            groups.setdefault((m.subject, m.scope, m.attribute, m.split), []).append((row, m))
        for entries in groups.values():
            for i, (row, m) in enumerate(entries):
                successors = [(r, n) for r, n in entries[i + 1:] if n.updates_preference and n.value != m.value]
                next_row, next_memory = successors[0] if successors else (None, None)
                status = "superseded" if next_row else "confirmed"
                valid_to = next_memory.recorded_at if next_memory else None
                # Keep the reviewed payload unchanged for idempotent re-import.
                self.conn.execute("UPDATE memories SET status=?,valid_to=? WHERE id=?", (status, valid_to, row["id"]))
                if row["fact_id"]:
                    self.conn.execute("UPDATE facts SET status=?,valid_to=?,superseded_by=? WHERE id=?", ("superseded" if next_row else "active", valid_to, next_row["fact_id"] if next_row else None, row["fact_id"]))
                    if row["status"] != status or row["valid_to"] != valid_to:
                        self.repo._bump("fact")
                        self.repo._bump("memory")

    def query(self, keyword="", *, subject="self", category=None, basis="direct", scope=None, as_of=None, split="train", include_candidates=False, include_expired=False):
        rows = self.conn.execute("SELECT * FROM memories WHERE subject=? AND basis=? AND (? IS NULL OR category=?) AND (? IS NULL OR scope=?) AND (? IS NULL OR split=?) ORDER BY recorded_at,id", (subject, basis, category, category, scope, scope, split, split)).fetchall()
        now = int(time.time()) if as_of is None else as_of
        result = []
        for row in rows:
            m = Memory.model_validate_json(row["payload"])
            m.status, m.valid_to = row["status"], row["valid_to"]
            if keyword not in m.value and keyword not in m.summary:
                continue
            if m.status in ("rejected", "unknown") or (m.status == "candidate" and not include_candidates):
                continue
            known_from = m.valid_from if m.valid_from is not None else m.recorded_at
            if not include_expired and (known_from > now or (m.valid_to is not None and m.valid_to <= now)):
                continue
            if as_of is None and not include_expired and m.status == "superseded":
                continue
            if as_of is None and not include_expired and m.category == "plan" and m.valid_to is None:
                continue
            result.append(m)
        return result

    def export(self, path: Path, *, split="train"):
        """Snapshot export; regenerating/removing this file follows DB maintenance.

        Never export inference, rejected, expired plans or other-person records
        to the default self knowledge corpus. Every paragraph has source IDs.
        """
        rows = self.conn.execute("SELECT * FROM memories WHERE status='confirmed' AND basis='direct' AND split=? AND subject IN ('self','conversation') ORDER BY recorded_at,id", (split,)).fetchall()
        parts = ["# 本地已复核记忆\n\n范围：训练事件；事件为原文摘录，计划不代表完成。关系未知。"]
        ids = set()
        for row in rows:
            m = Memory.model_validate_json(row["payload"])
            if m.category == "plan" and (m.valid_to is None or m.valid_to <= int(time.time())):
                continue
            labels = "; ".join(f"{e.batch_id}/{e.source_id} (app:{e.message_id}, speaker:{e.speaker_id}, time:{e.timestamp})" for e in m.evidence)
            body = m.summary or m.value
            parts.append(f"## {m.entry_key}\n\n类别：{m.category}；主体：{m.subject}；关系范围：{m.scope}；发生时间：{m.occurred_at}；记录时间：{m.recorded_at}\n\n{body}\n\n来源：{labels}")
            ids.update(e.message_id for e in m.evidence)
        content = "\n\n".join(parts) + "\n"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep managed DB documents and indexes in sync; the file is an export.
        with self.conn:
            old = self.conn.execute("SELECT id FROM knowledge_documents WHERE name=?", (str(path),)).fetchone()
            if old:
                self.conn.execute("DELETE FROM knowledge_documents WHERE id=?", (old[0],))
            if ids:
                self.repo._source_ids(sorted(ids))
                did = self.conn.execute("INSERT INTO knowledge_documents(name,content,purpose) VALUES(?,?,'reviewed_memory_train')", (str(path),content)).lastrowid
                self.conn.executemany("INSERT INTO document_sources VALUES(?,?)", [(did,i) for i in sorted(ids)])
                for ordinal,start in enumerate(range(0,len(content),400)):
                    self.conn.execute("INSERT INTO chunks(document_id,ordinal,content) VALUES(?,?,?)", (did,ordinal,content[start:start+500]))
            self.repo._bump("document")
        path.write_text(content, encoding="utf-8")
        return {"memories": len(parts) - 1, "sources": len(ids), "path": str(path)}


def review_record(memory, status="confirmed", note="local_codex_review"):
    return {"status": status, "reviewer": "codex_local", "note": note,
            **{k: getattr(memory, k) for k in ("category", "subject", "attribute", "value", "summary")}}
