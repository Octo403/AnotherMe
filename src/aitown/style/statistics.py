import re
from collections import Counter, defaultdict
from statistics import mean, median

from .models import Scene

TOKENS = ("嗯", "嗯嗯", "哈哈", "好滴", "好呀", "对的", "确实", "没招了", "天呐", "欸", "诶", "谢谢", "感谢", "不好意思", "请")
EMOJI = re.compile(r"\[[^\]\n]{1,12}\]|[\U0001F300-\U0001FAFF\u2600-\u27BF]")
QUESTION = re.compile(r"[?？]|(?:吗|么|咋了|哪个区|在哪里|什么时候)[。！!\s]*$")


def response_scenes(text) -> list[Scene]:
    result = []
    if re.search(r"不行|不来|去不了|那不会|不要|不方便|没空", text): result.append("refuse")
    if re.search(r"没事|加油|冲冲|可怜|别急|会好的|别担心", text): result.append("comfort")
    if re.search(r"不知道|不确定|可能|好像|应该|没了解|想不到|不记得", text): result.append("uncertain")
    if re.search(r"因为|所以|就是|里面|首先|另存|百分|区别|估计", text): result.append("explain")
    if re.search(r"作业|组队|资料|比赛|课|报名|任务|考试|老师|文件|会议|计划", text): result.append("task")
    if re.search(r"^(?:好|嗯|对|可以|行|OK|ok|确实|来来)", text): result.append("agree")
    return result or ["chat"]


def request_scene(text) -> Scene:
    if re.search(r"难过|不开心|失败|没考好|压力|焦虑|担心|搞砸|累死", text): return "comfort"
    if re.search(r"拒绝|不想(?:去|参加|报名)|不参加|不报名|不方便|没空", text): return "refuse"
    if re.search(r"怎么|为什么|区别|解释", text): return "explain"
    if re.search(r"知道|记得|确定|有把握", text): return "uncertain"
    if re.search(r"作业|组队|任务|文件|会议|资料|考试|安排", text): return "task"
    if re.search(r"一起|可以吗|愿意|好不好", text): return "agree"
    return "chat"


def guidance(text):
    """Abstract form and bounded common words; never emit historical facts."""
    words = [t for t in TOKENS if t in text]
    return (f"回复约 {len(text)} 字，{len(text.splitlines())} 行；"
            f"问句线索：{'有' if QUESTION.search(text) else '无'}；"
            f"表情：{'有' if EMOJI.search(text) else '无'}；"
            f"可参考口语：{'、'.join(words[:4]) or '无特定口语'}。")


def training_rows(repo):
    return repo.conn.execute("SELECT s.*,e.conversation_id FROM style_examples s JOIN episodes e ON e.id=s.episode_id WHERE s.split='train' ORDER BY s.id").fetchall()


def statistics(repo):
    groups = defaultdict(list)
    for row in training_rows(repo): groups[(row["conversation_id"],row["scene"])].append(row)
    report = {"split":"train", "relationship_policy":"unknown; conversation scope is not a guessed relationship", "groups":[]}
    for (scope, scene), rows in groups.items():
        texts = [r["target"] for r in rows]
        lengths = [len(t) for t in texts]
        tokens = {t:sum(text.count(t) for text in texts) for t in TOKENS}
        source_records = {}
        consecutive = 0
        for row in rows:
            sources = repo.sources("style",row["id"])
            # Only target messages are counted once; context can overlap samples.
            ids = {r[0] for r in repo.conn.execute("SELECT message_id FROM style_sources WHERE style_id=? AND role='target'",(row["id"],))}
            consecutive += len(ids)>1
            for source in sources:
                if source.message_id in ids:
                    if source.direction != "outgoing" or source.speaker_id != "self": raise ValueError("Style target is not self")
                    source_records[source.message_id] = source.model_dump()
        report["groups"].append({"scope":scope,"scene":scene,"relationship":"unknown","replies":len(rows),
            "mean_chars":round(mean(lengths),2),"median_chars":median(lengths),"short_replies_le12":sum(n<=12 for n in lengths),
            "multiline_replies":sum('\n' in t for t in texts),"emoji_replies":sum(bool(EMOJI.search(t)) for t in texts),
            "question_replies":sum(bool(QUESTION.search(t)) for t in texts),
            "question_mark_replies":sum(bool(re.search('[?？]',t)) for t in texts),
            "punctuation":dict(Counter(c for t in texts for c in t if c in '，。！？,.!?')),
            "tokens":tokens,"polite_replies":sum(bool(re.search('谢谢|感谢|不好意思|请',t)) for t in texts),
            "response_scenes":dict(Counter(s for t in texts for s in response_scenes(t))),
            "target_messages":len(source_records),"consecutive_message_replies":consecutive,
            "source_ids":sorted(source_records),"examples":[{"style_id":r["id"],"target":r["target"]} for r in rows[:5]]})
    report["replies"] = sum(g["replies"] for g in report["groups"])
    return report
