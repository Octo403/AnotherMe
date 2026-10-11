"""Conservative local episode and response-example construction."""
import re
from collections import Counter

from pydantic import BaseModel, Field

from .selection import MISSING


class Parameters(BaseModel):
    merge_seconds: int = Field(default=60, ge=0)
    episode_seconds: int = Field(default=1800, gt=0)
    topic_seconds: int = Field(default=300, ge=0)
    ambiguous_seconds: int = Field(default=120, ge=0)
    context_turns: int = Field(default=6, ge=1)
    require_context_anchor: bool = True


def plain(row):
    return (row["conversation_kind"] == "private" and row["type"] == 1
            and row["status"] == "text" and row["eligible_text"]
            and row["direction"] in {"incoming", "outgoing"} and bool(row["content"].strip()))


def merge(rows, params, references):
    turns = []
    for row in rows:
        previous = turns[-1] if turns else None
        can_merge = (previous and plain(row) and previous["plain"]
            and row["conversation_id"] == previous["conversation_id"]
            and row["batch_id"] == previous["batch_id"]
            and row["speaker_id"] == previous["speaker_id"]
            and row["direction"] == previous["direction"]
            and row["day"] == previous["day"]
            and row["timestamp"] - previous["end"] <= params.merge_seconds
            and not references.get(str(row["id"])))
        if can_merge:
            previous["message_ids"].append(row["id"])
            previous["lines"].append(row["content"])
            previous["end"] = row["timestamp"]
            previous["text"] = "\n".join(previous["lines"])
        else:
            turns.append({"turn_id": f"{row['conversation_id']}-t{row['id']}",
                "batch_id": row["batch_id"], "conversation_id": row["conversation_id"],
                "message_ids": [row["id"]], "speaker_id": row["speaker_id"],
                "direction": row["direction"], "start": row["timestamp"], "end": row["timestamp"],
                "day": row["day"], "status": row["status"], "type": row["type"],
                "plain": plain(row), "lines": [row["content"]], "text": row["content"]})
    return turns


def topic_change(a, b, gap, params):
    if re.match(r"(?:对了|另外[，,：:]|换个话题|说个别的)", b["text"]):
        return True
    if gap < params.topic_seconds or min(len(a["text"]), len(b["text"])) < 20:
        return False
    aa = set(re.findall(r"[\u4e00-\u9fff]", a["text"]))
    bb = set(re.findall(r"[\u4e00-\u9fff]", b["text"]))
    return bool(aa and bb) and len(aa & bb) / len(aa | bb) < .03


def split_episodes(turns, params, references):
    episodes, current, previous = [], None, None
    for turn in turns:
        reasons = []
        if previous:
            gap = turn["start"] - previous["end"]
            if turn["conversation_id"] != previous["conversation_id"]:
                reasons.append("conversation_change")
            if turn["day"] != previous["day"]:
                reasons.append("date_change")
            if gap > params.episode_seconds:
                reasons.append("time_gap")
            if not turn["plain"] or not previous["plain"]:
                reasons.append("unavailable_or_non_plain")
            if previous["plain"] and turn["plain"] and topic_change(previous, turn, gap, params):
                reasons.append("topic_change")
            # An exact backward citation can join long-gap/date-separated messages.
            ref = references.get(str(turn["message_ids"][0]), {})
            if current and ref.get("target_id") in current["message_ids"] and ref.get("status") == "resolved":
                reasons = [r for r in reasons if r not in {"time_gap", "date_change", "topic_change"}]
        if current is None or reasons:
            current = {"episode_id": f"{turn['conversation_id']}-e{turn['message_ids'][0]}",
                "conversation_id": turn["conversation_id"], "start": turn["start"], "end": turn["end"],
                "turn_ids": [], "message_ids": [], "boundary_reasons": reasons or ["start"],
                "missing_message_ids": []}
            episodes.append(current)
        current["turn_ids"].append(turn["turn_id"])
        current["message_ids"].extend(turn["message_ids"])
        current["end"] = turn["end"]
        if not turn["plain"]:
            current["missing_message_ids"].extend(turn["message_ids"])
        previous = turn
    return episodes


AMBIGUOUS = re.compile(r"^(?:可以|好|好的|行|嗯|对|是|是的|没有|不行|没|ok|OK)[。.!！?？~～ ]*$")


def examples(episodes, turns, references, reviewed, scenes, params):
    by_turn = {t["turn_id"]: t for t in turns}
    by_message = {i: t for t in turns for i in t["message_ids"]}
    accepted, proposals, exclusions, links = [], [], Counter(), []
    for episode in episodes:
        history = []
        for tid in episode["turn_ids"]:
            target = by_turn[tid]
            if target["direction"] != "outgoing" or not target["plain"]:
                history.append(target)
                continue
            ref = references.get(str(target["message_ids"][0]))
            context = history[-params.context_turns:]
            reasons = []
            incoming = next((t for t in reversed(context) if t["direction"] == "incoming"), None)
            if ref:
                if ref["status"] == "resolved":
                    cited = by_message.get(ref["target_id"])
                    if (cited and (cited["start"], cited["message_ids"][0]) < (target["start"], target["message_ids"][0])
                            and cited["conversation_id"] == target["conversation_id"]):
                        if cited not in context:
                            context = [cited, *context]
                    else:
                        reasons.append("reference_unavailable_or_not_prior")
                else:
                    reasons.append("reference_"+ref["status"])
            incoming = next((t for t in reversed(context) if t["direction"] == "incoming"), None)
            if not context or incoming is None:
                reasons.append("no_prior_incoming_context")
            if params.require_context_anchor and context:
                # An orphan pronoun immediately after a missing source is not an antecedent.
                first_text = context[0]["text"]
                substantive = re.sub(r"\[[^\]]+\]|[^\u4e00-\u9fffA-Za-z0-9]", "", first_text)
                if not substantive:
                    reasons.append("no_substantive_context")
                if (len(first_text) <= 20 and re.search(r"这个|那个|这些|这样|这俩|样子|那份",first_text)
                        and "unavailable_or_non_plain" in episode["boundary_reasons"]):
                    reasons.append("orphan_deictic_context")
            if any(not t["plain"] for t in context):
                reasons.append("missing_context")
            if incoming and AMBIGUOUS.fullmatch(target["text"].strip()) and not (ref and ref["status"] == "resolved"):
                if target["start"] - incoming["end"] > params.ambiguous_seconds:
                    reasons.append("ambiguous_asynchronous_reply")
            ids = [i for t in context for i in t["message_ids"]]
            quality = {"episode_id": episode["episode_id"], "target_message_ids": target["message_ids"],
                       "status": "ambiguous" if "ambiguous_asynchronous_reply" in reasons else
                                 "explicit_reference" if ref else "adjacent_candidate",
                       "reference": ref, "reasons": list(reasons)}
            links.append(quality)
            if not reasons:
                record = {"example_id": target["turn_id"], "batch_id": target["batch_id"],
                    "episode_id": episode["episode_id"], "conversation_id": target["conversation_id"],
                    "scene": scenes.get(target["conversation_id"], "unknown"),
                    "target_message_ids": target["message_ids"], "context_message_ids": ids,
                    "target_start": target["start"], "target_end": target["end"],
                    "messages": [{"role": "user" if t["direction"] == "incoming" else "assistant",
                                  "content": t["text"], "source_ids": t["message_ids"]} for t in context],
                    "target": target["text"], "attachment_dependent": False,
                    "quality_labels": [quality["status"]], "reference": ref}
                proposals.append(record)
                if any(reviewed.get(str(i)) != "approved" for i in [*ids, *target["message_ids"]]):
                    reasons.append("privacy_unreviewed_or_excluded")
                else:
                    accepted.append(record)
            exclusions.update(reasons)
            quality["reasons"] = list(reasons)
            history.append(target)
    return accepted, proposals, exclusions, links


def partition(episodes, examples, references):
    parents = {e["episode_id"]: e["episode_id"] for e in episodes}
    message_episode = {m:e["episode_id"] for e in episodes for m in e["message_ids"]}
    def root(e):
        while parents[e] != e:
            parents[e] = parents[parents[e]]
            e = parents[e]
        return e
    for mid, ref in references.items():
        a, b = message_episode.get(int(mid)), message_episode.get(ref.get("target_id"))
        if a and b and ref["status"] == "resolved":
            parents[root(a)] = root(b)
    groups = {}
    for e in episodes:
        groups.setdefault(root(e["episode_id"]), []).append(e)
    groups = sorted(groups.values(), key=lambda group: (max(e["end"] for e in group), min(e["episode_id"] for e in group)))
    n = len(groups)
    # Whole components, chronological allocation. Small datasets cannot promise exact ratios.
    a, b = int(n*.8), int(n*.9)
    allocation = {e["episode_id"]: "train" if i<a else "dev" if i<b else "test"
                  for i, group in enumerate(groups) for e in group}
    splits = {name: [] for name in ("train", "dev", "test")}
    for example in examples:
        row = {**example, "split": allocation[example["episode_id"]]}
        splits[row["split"]].append(row)
    return splits, {"method": "chronological_whole_reference_connected_episode_components",
                    "allocation": allocation, "component_count": n,
                    "episode_counts": dict(Counter(allocation.values())),
                    "example_counts": {k:len(v) for k,v in splits.items()}}
