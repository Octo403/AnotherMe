"""Source-key deduplication and stable ordering without text fingerprints."""
import datetime as dt

from ..wechat_clean import ZONE


def normalize(rows, batch):
    seen, ordered, issues = {}, [], []
    duplicates = 0
    for raw in rows:
        row = dict(raw)
        row["batch_id"] = row.get("batch_id", batch)
        key = (row["batch_id"], row["id"])
        if key in seen:
            if seen[key] != row:
                raise ValueError(f"Conflicting source record: {key}")
            duplicates += 1
            continue
        seen[key] = dict(row)
        timestamp = row.get("timestamp")
        try:
            if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
                raise ValueError("invalid timestamp")
            row["day"] = dt.datetime.fromtimestamp(timestamp, ZONE).date().isoformat()
        except (ValueError, OverflowError, OSError):
            issues.append({"batch_id": row["batch_id"], "message_id": row["id"],
                           "reason": "invalid_timestamp", "row": row})
            continue
        ordered.append(row)
    ordered.sort(key=lambda r: (r["conversation_id"], r["timestamp"], r["id"], r["batch_id"]))
    return ordered, {"input": len(seen)+duplicates, "duplicates": duplicates,
                     "normalized": len(ordered), "quarantined": issues}
