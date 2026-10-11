"""Conservative Chinese rules. Exact excerpts replace unconstrained summaries.

Only reviewed plain messages are interpreted. Direction AND speaker determine
the author; quotes, reported speech, jokes and ambiguous context stay unknown.
No resolved pronouns, invented completion, personality or health diagnosis.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .models import Evidence, Memory

TZ = timezone(timedelta(hours=8))
UNCERTAIN = re.compile(r"[?？]|[\"“”‘’「」]|他说|她说|他说过|她说过|据说|听说|开玩笑|哈哈|笑死|反话|假如|如果|可能|也许|应该|似乎|好像|比如|例如|🙃|狗头|[<>]")
PREFERENCE = re.compile(r"^我(?P<update>现在|目前|最近|不再|更)?(?P<verb>不喜欢|喜欢|更喜欢|偏好)(?P<value>[^，。！!；;\n]{1,35})[。！!]*$")
LONG_TERM = re.compile(r"^我(?:是(?P<identity>[^，。！!\n]{1,25}(?:专业的学生|专业学生|研究生|本科生|老师|教师|程序员))|在(?P<school>[^，。！!\n]{1,25}(?:读书|上学|读研|读本科)))[。！!]*$")
PLAN = re.compile(r"^(?:我(?:明天|后天|下周|计划|打算|准备|想要)|我今天(?:计划|打算|准备)|(?:明天|后天)(?:就|准备)?(?:去|下山|上山|填|交)|今天准备|(?:准备|打算|决定)(?:去|放弃))")
EVENT = re.compile(r"^我(?:昨天|前天|今天|刚刚|已经)?.*(?:去了|去过|参加了|完成了|做完了|交了|回来了|看了|买了)")
NEGATIVE_INTEREST = re.compile(r"^(?:而且)?我(?:个人)?对(?P<value>[^，。！!\n]{1,25}?)(?:也)?(?:不是很感冒|不感兴趣)[。！!]*$")
BIRTHDAY = re.compile(r"^(?P<who>我|[a-z]{1,8})(?:的)?生日(?:是|在)(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号。]*$")


def relative_time(text, timestamp):
    day = datetime.fromtimestamp(timestamp, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    for word, offset in (("前天", -2), ("昨天", -1), ("后天", 2), ("明天", 1), ("今天", 0)):
        if word in text:
            return int((day + timedelta(days=offset)).timestamp())
    # "下周" and unspecified plans have no exact day; do not fabricate one.
    return None if re.search(r"下周|下个月|^(?:我)?(?:计划|打算|准备|想要|决定)|今年|以前|之前|当时", text) else timestamp


def extract_message(row, episode_id, split):
    if row["review_status"] != "approved" or row["type"] != 1 or row["status"] != "text":
        return None
    text = row["content"].strip()
    interpreted = re.sub(r"^(?:没有没有|没有|嗯|对)[ ，,]+(?=我)", "", text)
    evidence = Evidence(message_id=row["id"], batch_id=row["batch_id"], source_id=row["source_id"],
                        timestamp=row["timestamp"], direction=row["direction"], speaker_id=row["speaker_id"], excerpt=row["content"])
    author = ("self" if row["direction"] == "outgoing" and row["speaker_id"] == "self"
              else "other:" + row["conversation_id"] if row["direction"] == "incoming" and row["speaker_id"] != "self" else "unknown")
    base = dict(entry_key=f"message:{row['batch_id']}:{row['source_id']}", episode_id=episode_id,
                recorded_at=row["timestamp"], valid_from=row["timestamp"], scope=row["conversation_id"],
                split=split, evidence=[evidence], summary=text)
    if UNCERTAIN.search(text) or author == "unknown":
        if re.search(r"喜欢|生日|我(?:明天|昨天|是|在|计划|打算)|他说|她说", text):
            return Memory(category="unknown", status="unknown", notes=["quotation_question_report_or_uncertain"], **base)
        return None
    preference, stable, birthday = PREFERENCE.fullmatch(interpreted), LONG_TERM.fullmatch(interpreted), BIRTHDAY.fullmatch(interpreted)
    negative = NEGATIVE_INTEREST.fullmatch(interpreted)
    category, attribute, value, update = "unknown", "", "", False
    if preference:
        category, attribute = "preference", "preference:" + preference["value"]
        for axis, words in (("drink", ("咖啡", "茶", "牛奶")), ("sport", ("跑步", "游泳", "篮球", "足球"))):
            if any(word in preference["value"] for word in words):
                attribute = "preference:" + axis
                break
        value = preference["verb"] + preference["value"]
        update = bool(preference["update"] or preference["verb"] == "更喜欢")
    elif negative:
        category, attribute, value = "preference", "preference:" + negative["value"], "不感兴趣：" + negative["value"]
    elif stable:
        category, attribute, value = "long_term", "education_or_occupation", text
    elif birthday:
        try:
            datetime(2000, int(birthday["month"]), int(birthday["day"]))
        except ValueError:
            return Memory(category="unknown", status="unknown", notes=["invalid_birthday"], **base)
        category, attribute, value = "long_term", "birthday", f"{int(birthday['month']):02}-{int(birthday['day']):02}"
        if birthday["who"] != "我":
            author = "other:" + row["conversation_id"] + ":" + birthday["who"]
    elif EVENT.search(interpreted) and not re.search(r"没|未|不曾|明天|后天|下周|^我(?:计划|准备|打算)", interpreted):
        category, attribute, value = "event", "completed_event", text
    elif PLAN.search(interpreted):
        category, attribute, value = "plan", "intention", text
    else:
        if re.search(r"^我今天去|^我.*(?:没|未|不曾).*(?:参加|完成|做完|去)", interpreted):
            return Memory(category="unknown", status="unknown", notes=["ambiguous_tense_or_negative_completion"], **base)
        return None
    occurred = relative_time(text, row["timestamp"]) if category in ("event", "plan") else row["timestamp"]
    if category == "plan" and occurred is not None:
        base["valid_to"] = occurred + 86400
    if author != "self":
        original = category
        category = "other_person"
        base["notes"] = ["other_category:" + original]
    return Memory(category=category, subject=author, basis="direct", attribute=attribute,
                  value=value, occurred_at=occurred, updates_preference=update, **base)


def extract_batch(repo, limit=None):
    episodes = repo.conn.execute("SELECT e.* FROM episodes e WHERE EXISTS(SELECT 1 FROM episode_messages em JOIN message_refs m ON m.id=em.message_id WHERE em.episode_id=e.id AND m.review_status='approved' AND m.type=1 AND m.status='text') ORDER BY e.start,e.id").fetchall()
    result, topics = [], defaultdict(list)
    # An episode related to any held-out style example is excluded from train
    # even if another example in it is train. Unassigned episodes stay private.
    for episode in episodes[:limit] if limit is not None else episodes:
        splits = {r[0] for r in repo.conn.execute("SELECT split FROM style_examples WHERE episode_id=?", (episode["id"],))}
        split = "test" if "test" in splits else "dev" if "dev" in splits else "train" if splits == {"train"} else "unassigned"
        rows = repo.conn.execute("SELECT m.* FROM episode_messages e JOIN message_refs m ON m.id=e.message_id WHERE e.episode_id=? ORDER BY e.ordinal", (episode["id"],)).fetchall()
        readable = [r for r in rows if r["review_status"] == "approved" and r["type"] == 1 and r["status"] == "text"]
        candidates = {r["id"]: extract_message(r, episode["id"], split) for r in readable}
        # Favor explicit statements and retain both episode edges; source order
        # is restored before rendering. All other originals remain in T4.
        ranked = sorted(enumerate(readable), key=lambda item: (bool(candidates[item[1]["id"]]), item[0] < 2 or item[0] >= len(readable)-2), reverse=True)[:6]
        selected = [r for _, r in sorted(ranked)]
        evidence = [Evidence(message_id=r["id"], batch_id=r["batch_id"], source_id=r["source_id"],
                             timestamp=r["timestamp"], direction=r["direction"], speaker_id=r["speaker_id"], excerpt=r["content"]) for r in selected]
        if evidence:
            summary = "\n".join(f"{e.speaker_id}: {e.excerpt}" for e in evidence)
            result.append(Memory(entry_key=f"episode:{episode['batch_id']}:{episode['source_id']}", episode_id=episode["id"],
                                 category="event", subject="conversation", attribute="source_excerpt", value=summary,
                                 summary=summary, occurred_at=episode["start"], recorded_at=episode["end"], scope=episode["conversation_id"],
                                 split=split, basis="direct", evidence=evidence, notes=["excerpt_only", "partial_episode" if len(evidence) != len(rows) else "complete_excerpt"]))
        for row in readable:
            candidate = candidates[row["id"]]
            if candidate:
                result.append(candidate)
            if row["direction"] == "outgoing" and row["speaker_id"] == "self":
                for topic in ("实验", "论文", "作业", "旅行", "游戏", "考试", "大学"):
                    if topic in row["content"]:
                        topics[(row["conversation_id"], topic)].append((episode["id"], split, row))
    for (scope, topic), mentions in topics.items():
        if len({e for e, _, _ in mentions}) < 3:
            continue
        evidence = [Evidence(message_id=r["id"], batch_id=r["batch_id"], source_id=r["source_id"], timestamp=r["timestamp"], direction=r["direction"], speaker_id=r["speaker_id"], excerpt=r["content"]) for _, _, r in mentions]
        splits = {s for _, s, _ in mentions}
        result.append(Memory(entry_key=f"topic:{scope}:{topic}", category="inference", subject="self", attribute="repeated_topic",
                             value=topic, summary=f"在 {len({e for e, _, _ in mentions})} 个事件提到“{topic}”，仅作话题候选。",
                             basis="inferred", recorded_at=max(e.timestamp for e in evidence), scope=scope,
                             split="train" if splits == {"train"} else "unassigned", evidence=evidence,
                             notes=["not_a_hobby_personality_or_health_claim"]))
    return result
