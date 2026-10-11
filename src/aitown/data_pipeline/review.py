"""T2 local review artifacts. Logs contain counts and pseudonymous IDs only."""
import argparse
import datetime as dt
import json
import random
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .selection import select
from ..wechat_clean import MEDIA, ZONE, contacts_from_export


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def connect(path):
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn, cid=None):
    sql = "SELECT m.*, c.kind AS conversation_kind FROM messages m JOIN conversations c ON c.id=m.conversation_id"
    if cid:
        sql += " WHERE c.id=?"
    sql += " ORDER BY m.conversation_id,m.timestamp,m.id"
    return conn.execute(sql, (cid,) if cid else ())


def distribution(conn):
    dims = {key: Counter() for key in ("month", "direction", "type", "status", "kind")}
    cross = Counter()
    conversations = {}
    for row in rows(conn):
        month = dt.datetime.fromtimestamp(row["timestamp"], ZONE).strftime("%Y-%m")
        values = (month, row["direction"], str(row["type"]), row["status"], row["conversation_kind"])
        for key, value in zip(dims, values):
            dims[key][value] += 1
        cross[values] += 1
        info = conversations.setdefault(row["conversation_id"], {
            "id": row["conversation_id"], "kind": row["conversation_kind"], "total": 0,
            "plain_text": 0, "outgoing_text": 0, "compressed": 0, "media": 0,
            "longest_missing_run": 0, "_run": 0})
        info["total"] += 1
        readable = row["status"] == "text" and bool(row["eligible_text"])
        info["plain_text"] += readable
        info["outgoing_text"] += readable and row["direction"] == "outgoing"
        info["compressed"] += row["status"] == "compressed_unresolved"
        # Many media records are also compressed; count their known type as media.
        info["media"] += row["status"] == "media_omitted" or row["type"] in MEDIA
        info["_run"] = 0 if readable else info["_run"] + 1
        info["longest_missing_run"] = max(info["longest_missing_run"], info["_run"])
    for info in conversations.values():
        info.pop("_run")
        info["readable_ratio"] = round(info["plain_text"] / info["total"], 4)
        info["compressed_heavy"] = info["compressed"] / info["total"] >= .3
        info["media_dependent"] = info["media"] / info["total"] >= .2 or info["longest_missing_run"] >= 10
    total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert all(sum(value.values()) == total for value in dims.values())
    return {"total": total, "conversation_count": len(conversations),
            "dimensions": dims, "cross_counts": [{"month": k[0], "direction": k[1], "type": k[2],
            "status": k[3], "kind": k[4], "count": v} for k, v in sorted(cross.items())],
            "conversations": list(conversations.values()), "totals_match_database": True}


def prepare(conn, output, source, aliases):
    dist = distribution(conn)
    write_json(output / "data_distribution.json", dist)
    ranked = sorted((c for c in dist["conversations"] if c["kind"] == "private"
                    and c["outgoing_text"] >= 20 and c["plain_text"] >= 100),
                    key=lambda c: (c["readable_ratio"], c["outgoing_text"]), reverse=True)
    first = ranked[:4]
    write_json(output / "first_conversations.json", {"selection": "readable_ratio_then_outgoing_count",
        "conversations": [{**c, "relationship": "unknown", "scene": "unknown"} for c in first]})
    candidates = defaultdict(list)
    pool = list(rows(conn))
    by_cid = defaultdict(list)
    for row in pool:
        by_cid[row["conversation_id"]].append(dict(row))
    for cid, items in by_cid.items():
        for index, row in enumerate(items):
            if row["status"] != "text":
                continue
            text = row["content"]
            if cid in {c["id"] for c in first}:
                candidates["private"].append((cid, index))
            if row["conversation_kind"] == "group":
                candidates["group"].append((cid, index))
            if re.search(r"密码|口令|验证码|密钥|地址|账号|身份证|手机号", text):
                candidates["sensitive"].append((cid, index))
            if index and re.search(r"密码|口令|验证码|密钥", items[index-1]["content"]):
                candidates["cross_message_secret"].append((cid, index))
            if row["conversation_kind"] == "private" and not re.search(r"密码|地址|账号|编号|手机号", text):
                candidates["ordinary"].append((cid, index))
    rng = random.Random(20261004)
    snippets, used = [], set()
    for category in ("private", "group", "sensitive", "cross_message_secret", "ordinary"):
        rng.shuffle(candidates[category])
        for cid, index in candidates[category]:
            if (cid, index) in used:
                continue
            used.add((cid, index))
            window = by_cid[cid][max(0,index-1):index+2]
            snippets.append({"snippet_id": len(snippets)+1, "category": category,
                             "center_id": by_cid[cid][index]["id"], "messages": window,
                             "review_status": "pending", "issues": []})
            if sum(s["category"] == category for s in snippets) == 20:
                break
    write_json(output / "review_snippets.json", snippets)
    names, contacts = contacts_from_export(source, aliases)
    reverse = defaultdict(list)
    for name, alias in names.items():
        reverse[alias].append(name)
    write_json(output / "mapping_audit.json", {"aliases": names, "contacts": contacts,
        "collisions": {a:n for a,n in reverse.items() if len(n)>1},
        "note": "Aliases are display labels, never identity keys; conversation IDs remain distinct."})
    print(json.dumps({"records": dist["total"], "conversations": dist["conversation_count"],
                      "snippets": len(snippets), "first_ids": [c["id"] for c in first]}))


def finalize(conn, output):
    snippets = json.loads((output / "review_snippets.json").read_text(encoding="utf-8"))
    reviewed, issues = {}, Counter()
    priority = {"approved": 0, "excluded": 1, "pending": 2}
    for snippet in snippets:
        status = snippet["review_status"]
        issues.update(snippet["issues"])
        for row in snippet["messages"]:
            key = str(row["id"])
            if key not in reviewed or priority[status] > priority[reviewed[key]]:
                reviewed[key] = status
    write_json(output / "message_reviews.json", reviewed)
    first = json.loads((output / "first_conversations.json").read_text(encoding="utf-8"))
    counts = Counter()
    per_conversation = {}
    with (output / "candidate_messages.jsonl").open("w", encoding="utf-8") as stream, \
         (output / "quality_labels.jsonl").open("w", encoding="utf-8") as labels:
        for conversation in first["conversations"]:
            local = Counter()
            for row, result in select(rows(conn, conversation["id"]), reviewed):
                counts["processed"] += 1
                counts.update(result["reasons"])
                local["processed"] += 1
                labels.write(json.dumps(result, ensure_ascii=False)+"\n")
                if result["selected"]:
                    record = dict(row)
                    record.update(result)
                    stream.write(json.dumps(record, ensure_ascii=False)+"\n")
                    counts[result["role"]] += 1
                    local[result["role"]] += 1
            per_conversation[conversation["id"]] = local
    write_json(output / "selection_statistics.json", {"counts": counts,
        "per_conversation": per_conversation, "reviewed_messages": len(reviewed), "issues": issues,
        "review_status_counts": Counter(s["review_status"] for s in snippets),
        "note": "Only locally reviewed messages are eligible. Candidate messages are not T3 dialogue examples."})
    print(json.dumps({"counts": counts, "reviewed_snippets": len(snippets), "issues": issues}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "finalize"])
    parser.add_argument("--database", type=Path, default=Path("data/processed/wechat_clean_20261004_t2/messages.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("data/private/review"))
    parser.add_argument("--source", type=Path, default=Path("WechatExport_20261003"))
    parser.add_argument("--aliases", type=Path, default=Path("data/private/wechat_name_aliases.json"))
    args = parser.parse_args()
    conn = connect(args.database)
    try:
        if args.action == "prepare":
            prepare(conn, args.output, args.source, args.aliases)
        else:
            finalize(conn, args.output)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
