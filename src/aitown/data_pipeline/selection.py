"""Conservative selection; readable text is not equivalent to reviewed text."""
import re
from collections import deque

MISSING = {"compressed_unresolved", "media_omitted", "unsupported"}
DEPENDENCY = re.compile(r"这个|那个|这样|那样|上面|附件|图片|照片|视频|语音|听一下|听听|看一下|看图|文件")


def assess(row, previous, reviewed):
    reasons = []
    if row["conversation_kind"] != "private":
        reasons.append("not_confirmed_private")
    if row["status"] != "text" or row["type"] != 1 or not row["eligible_text"]:
        reasons.append("not_readable_plain_text")
    if row["direction"] not in {"incoming", "outgoing"}:
        reasons.append("unknown_direction")
    if not row["content"].strip():
        reasons.append("empty_text")
    review = reviewed.get(str(row["id"]), "pending")
    if review != "approved":
        reasons.append("privacy_" + review)
    # Keep the actual missing source IDs; never manufacture attachment content.
    missing = [p["id"] for p in previous if p["status"] in MISSING
               and 0 <= row["timestamp"] - p["timestamp"] <= 1800]
    adjacent_missing = bool(previous and previous[-1]["id"] in missing)
    if missing and (adjacent_missing or DEPENDENCY.search(row["content"]) or len(row["content"].strip()) <= 8):
        reasons.append("insufficient_context")
    return {"message_id": row["id"], "conversation_id": row["conversation_id"],
            "role": "target" if row["direction"] == "outgoing" else "context",
            "selected": not reasons, "reasons": reasons,
            "missing_context_ids": missing,
            "quality": "insufficient_context" if "insufficient_context" in reasons else
                       "missing_nearby" if missing else "text_only"}


def select(rows, reviewed):
    history = deque(maxlen=8)
    conversation = None
    for row in rows:
        if row["conversation_id"] != conversation:
            history.clear()
            conversation = row["conversation_id"]
        result = assess(row, history, reviewed)
        yield row, result
        history.append(row)
