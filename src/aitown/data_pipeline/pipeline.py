"""Run T3 locally against the selected cleaned conversations."""
import argparse
import json
import random
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from .episodes import Parameters, examples, merge, partition, split_episodes
from .normalize import normalize
from .review import connect, rows, write_json


def write_lines(path, values):
    with path.open("w", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False)+"\n")


def references_from_source(source, selected):
    """Read only local reference metadata; no raw text or identifiers in outputs."""
    selected = {r["id"]: r for r in selected}
    raw_rows, lookup = {}, defaultdict(list)
    if source is None:
        return {str(r["id"]): {"status": "missing", "target_id": None}
                for r in selected.values() if r["type"] == 49}
    with source.open(encoding="utf-8-sig") as stream:
        for index, line in enumerate(stream, 1):
            if index not in selected:
                continue
            raw = json.loads(line)
            clean = selected[index]
            if raw["timestamp"] != clean["timestamp"] or raw["type"] != clean["type"] or raw["direction"] != clean["direction"]:
                raise ValueError("Raw reference source does not match selected cleaning batch")
            raw_rows[index] = raw
            sid = str(raw.get("server_id") or "")
            if sid and sid != "0":
                lookup[(clean["conversation_id"], sid)].append(index)
    result = {}
    for mid, raw in raw_rows.items():
        row = selected[mid]
        text = raw.get("content") or ""
        if raw.get("content_encoding") == "zstd":
            if row["type"] == 49:
                result[str(mid)] = {"status": "missing_compressed_structured", "target_id": None}
            continue
        if "<refermsg" not in text:
            continue
        try:
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                raise ValueError("unsupported XML")
            xml = ET.fromstring(text[text.index("<"):])
            sid = xml.findtext(".//refermsg/svrid")
            matches = lookup.get((row["conversation_id"], str(sid)), [])
            target = matches[0] if len(matches) == 1 else None
            status = "resolved" if target else "ambiguous" if matches else "missing"
            if target and (selected[target]["timestamp"],target) >= (row["timestamp"],mid):
                status, target = "not_prior", None
            result[str(mid)] = {"status": status, "target_id": target}
        except (ET.ParseError, ValueError):
            result[str(mid)] = {"status": "unsupported_reference", "target_id": None}
    return result


def run(database, selection, review_path, output, source=None, params=None):
    params = params or Parameters()
    manifest = json.loads(selection.read_text(encoding="utf-8"))
    reviewed = json.loads(review_path.read_text(encoding="utf-8"))
    conn = connect(database)
    try:
        selected = [dict(r) for c in manifest["conversations"] for r in rows(conn, c["id"])]
    finally:
        conn.close()
    normalized, normalization = normalize(selected, database.parent.name)
    references = references_from_source(source, normalized)
    turns = merge(normalized, params, references)
    episodes = split_episodes(turns, params, references)
    scenes = {c["id"]:c["scene"] for c in manifest["conversations"]}
    accepted, proposals, exclusions, links = examples(episodes, turns, references, reviewed, scenes, params)
    splits, assignment = partition(episodes, accepted, references)
    output.mkdir(parents=True, exist_ok=True)
    for name, values in (("normalized_messages",normalized),("turns",turns),("episodes",episodes),
                         ("dialogue_examples",accepted),("review_proposals",proposals),("reply_links",links)):
        write_lines(output / (name+".jsonl"), values)
    for name, values in splits.items():
        write_lines(output / (name+".jsonl"), values)
    # A training-only retrieval input never contains dev/test targets or their components.
    write_lines(output / "training_style_examples.jsonl", splits["train"])
    write_json(output / "references.json", references)
    write_json(output / "normalization.json", normalization)
    write_json(output / "split_assignment.json", assignment)
    write_json(output / "parameters.json", params.model_dump())
    rng = random.Random(20261004)
    review_episodes = rng.sample(episodes, min(50,len(episodes)))
    review_examples = rng.sample(proposals, min(100,len(proposals)))
    write_json(output / "episode_review_queue.json", review_episodes)
    write_json(output / "example_review_queue.json", review_examples)
    stats = {"messages":len(normalized),"turns":len(turns),"episodes":len(episodes),
             "boundary_reason_counts":dict(Counter(r for e in episodes for r in e["boundary_reasons"])),
             "proposed_examples":len(proposals),"accepted_examples":len(accepted),
             "exclusion_counts":dict(exclusions),"reference_status_counts":dict(Counter(r["status"] for r in references.values())),
             "scenes":dict(Counter(e["scene"] for e in accepted)),"splits":assignment["example_counts"],
             "episode_review_count":len(review_episodes),"example_review_count":len(review_examples)}
    write_json(output / "statistics.json", stats)
    print(json.dumps(stats))
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database",type=Path,default=Path("data/processed/wechat_clean_20261004_t2/messages.sqlite3"))
    parser.add_argument("--selection",type=Path,default=Path("data/private/review/first_conversations.json"))
    parser.add_argument("--reviews",type=Path,default=Path("data/private/review/message_reviews.json"))
    parser.add_argument("--output",type=Path,default=Path("data/private/t3"))
    parser.add_argument("--source",type=Path,default=Path("WechatExport_20261003/messages.jsonl"))
    for field, value in Parameters().model_dump().items():
        if isinstance(value, bool):
            continue
        parser.add_argument("--"+field.replace("_","-"),type=int,default=value)
    args = parser.parse_args()
    params = Parameters(**{field:getattr(args,field) for field in Parameters.model_fields if hasattr(args,field)})
    run(args.database,args.selection,args.reviews,args.output,args.source,params)


if __name__ == "__main__":
    main()
