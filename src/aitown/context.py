"""Assignment's multi-context isolation (not the standard MCP wire protocol)."""
import sqlite3
import time
from pathlib import Path


class ContextStore:
    def __init__(self, path: Path, turns: int = 8, ttl_hours: float = 168):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.turns = turns
        self.ttl = ttl_hours * 3600
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, "
            "session TEXT, agent_id TEXT, question TEXT, answer TEXT, created REAL)"
        )
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(turns)")}
        if "resident" in columns:
            self.conn.execute("ALTER TABLE turns RENAME COLUMN resident TO agent_id")
        self.conn.commit()

    def load(self, session: str, agent_id: str) -> list[dict[str, str]]:
        with self.conn:
            self.conn.execute("DELETE FROM turns WHERE created < ?", (time.time() - self.ttl,))
        rows = self.conn.execute(
            "SELECT question, answer FROM turns WHERE session=? AND agent_id=? "
            "ORDER BY id DESC LIMIT ?", (session, agent_id, self.turns)
        ).fetchall()
        return [message for question, answer in reversed(rows) for message in (
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        )]

    def save(self, session: str, agent_id: str, question: str, answer: str):
        with self.conn:
            self.conn.execute(
                "INSERT INTO turns(session,agent_id,question,answer,created) VALUES (?,?,?,?,?)",
                (session, agent_id, question, answer, time.time()),
            )
            self.conn.execute(
                "DELETE FROM turns WHERE session=? AND agent_id=? AND id NOT IN "
                "(SELECT id FROM turns WHERE session=? AND agent_id=? ORDER BY id DESC LIMIT ?)",
                (session, agent_id, session, agent_id, self.turns),
            )

    def close(self):
        self.conn.close()
