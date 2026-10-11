import json

from .chunker import document_chunks
from .models import Item


def items(repository, purpose):
    """Resolve current records and evidence in one SQLite read transaction."""
    conn = repository.conn
    result = []
    if purpose == "general":
        # Managed memory exports cannot bypass subject, split, or time filters.
        for row in conn.execute("SELECT * FROM knowledge_documents WHERE purpose='knowledge' ORDER BY id"):
            sources = repository.sources("document", row["id"])
            for ordinal, text in enumerate(document_chunks(row["content"])):
                result.append(Item(key=f"document:{row['id']}:{ordinal}", kind="document", entity_id=row["id"],
                                   ordinal=ordinal, text=text, sources=sources))
    elif purpose == "personal":
        for row in conn.execute("SELECT * FROM facts WHERE status IN ('active','superseded') ORDER BY id"):
            # Memory projections are represented once, by their structured record.
            if conn.execute("SELECT 1 FROM memories WHERE fact_id=?", (row["id"],)).fetchone():
                continue
            sources = repository.sources("fact", row["id"])
            if not sources:
                continue
            result.append(Item(key=f"fact:{row['id']}", kind="fact", entity_id=row["id"],
                               text=f"主体：{row['subject']}；属性：{row['attribute']}；{row['value']}",
                               subject=row["subject"], scopes=sorted({s.conversation_id for s in sources}),
                               timestamp=min(s.timestamp for s in sources), recorded_at=max(s.timestamp for s in sources),
                               valid_from=row["valid_from"], valid_to=row["valid_to"], status=row["status"], category=row['attribute'], sources=sources))
        for row in conn.execute("SELECT * FROM memories WHERE split='train' AND status IN ('confirmed','candidate','unknown','superseded') ORDER BY id"):
            payload = json.loads(row["payload"])
            sources = repository.sources("memory", row["id"])
            if not sources:
                continue
            body = payload.get("summary") or payload.get("value") or ""
            # One event/subject per record; do not join neighbouring memories.
            text = f"主体：{row['subject']}；类别：{row['category']}；发生时间：{row['occurred_at']}；记录时间：{row['recorded_at']}\n{body}"
            result.append(Item(key=f"memory:{row['id']}", kind="memory", entity_id=row["id"], text=text,
                               subject=row["subject"], scopes=[row["scope"]], timestamp=row["occurred_at"],
                               recorded_at=row["recorded_at"], valid_from=row["valid_from"], valid_to=row["valid_to"],
                               category=row["category"], basis=row["basis"], status=row["status"], sources=sources))
    elif purpose == "style":
        from ..style.statistics import response_scenes
        for row in conn.execute("SELECT s.*,e.conversation_id,e.start FROM style_examples s JOIN episodes e ON e.id=s.episode_id WHERE s.split='train' ORDER BY s.id"):
            context = json.loads(row["context"])
            text = "\n".join(message["content"] for message in context if message["role"] == "user")
            if text:
                result.append(Item(key=f"style:{row['id']}", kind="style", entity_id=row["id"], text=text,
                                   subject="self", scopes=[row["conversation_id"]], scene=row["scene"],
                                   response_scenes=response_scenes(row['target']), timestamp=row["start"],
                                   sources=repository.sources("style", row["id"])))
    return result
