"""Editable user feedback and curated, de-evented training demonstrations."""
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .models import Scene
from .statistics import request_scene


class FeedbackRule(BaseModel):
    key: str
    text: str
    scenes: list[Scene]
    rating_ids: list[str] = Field(min_length=1)
    ratings_path: Path | None = None
    purposes: list[Literal['reply','draft_refusal','acknowledge_refusal']] = Field(default_factory=list)
    enabled: bool = True
    omit_terminal_full_stop: bool = False


class Demonstration(BaseModel):
    style_id: int
    scene: Scene
    input: str
    reply: str
    note: str = '输入为去事件化改写，回复来自训练目标；仅示范表达，不提供事实'
    enabled: bool = True
    purpose: Literal['reply','draft_refusal','acknowledge_refusal'] = 'reply'


class FeedbackConfig(BaseModel):
    scope: str
    ratings_path: Path
    rules: list[FeedbackRule] = Field(default_factory=list)
    demonstrations: list[Demonstration] = Field(default_factory=list)


def load_feedback(path, repo, *, scope, question, scene=None):
    """No inferred relationship or fallback to another conversation's examples."""
    if not path or not scope:
        return {}
    config = FeedbackConfig.model_validate_json(Path(path).read_text(encoding='utf-8'))
    if config.scope != scope:
        return {}
    scene = scene or request_scene(question)
    task = response_task(question,scene)
    ratings_cache = {}
    rules = []
    for rule in config.rules:
        if not rule.enabled or scene not in rule.scenes or (rule.purposes and task['purpose'] not in rule.purposes):
            continue
        source = rule.ratings_path or config.ratings_path
        if source not in ratings_cache:
            ratings_cache[source] = json.loads(source.read_text(encoding='utf-8'))
        ratings = ratings_cache[source]
        annotations = {a['id']: a for c in ratings['cases'] for a in c['answers']}
        if not ratings.get('rater') or any(not annotations.get(i, {}).get('comment', '').strip() for i in rule.rating_ids):
            raise ValueError('Feedback rule requires actual user annotations')
        rules.append({'key': rule.key, 'text': rule.text, 'rating_ids': rule.rating_ids, 'omit_terminal_full_stop':rule.omit_terminal_full_stop})
    demos = []
    for demo in config.demonstrations:
        if not demo.enabled or demo.scene != scene:
            continue
        if scene == 'refuse' and demo.purpose != task['purpose']:
            continue
        row = repo.conn.execute("SELECT s.*,e.conversation_id FROM style_examples s JOIN episodes e ON e.id=s.episode_id WHERE s.id=? AND s.split='train'", (demo.style_id,)).fetchone()
        if row is None or row['conversation_id'] != scope or not repo.sources('style', demo.style_id):
            continue  # Removed source, held-out example or different scope is never reused.
        if not demo.reply.strip() or demo.reply not in row['target']:
            raise ValueError('Demonstration reply must be a reviewed training target excerpt')
        demos.append(demo.model_dump())
    return {'scope': scope, 'scene': scene, 'rules': rules, 'demonstrations': demos[:3]}


def format_feedback_answer(answer, feedback):
    """Explicit, opt-in terminal punctuation preference; never rewrite content."""
    if feedback.get('scene') not in ('agree','refuse','comfort','uncertain','chat'):
        return answer
    if not any(rule.get('omit_terminal_full_stop') for rule in feedback.get('rules',[])):
        return answer
    stripped=answer.rstrip()
    if stripped.endswith(('。','.')) and not stripped.endswith('...'):
        return stripped[:-1]
    return answer


def response_task(question, scene=None):
    scene = scene or request_scene(question)
    delegated = bool(re.search(r'帮我|替我|代我|代写|该怎么说|怎样拒绝|怎么回复|怎么回', question))
    reported = re.search(r'(?:对方|同学|朋友|他|她)说([^，,。；\n]*)',question)
    refusal_words = r'不参加|不想(?:去|参加|报名)|不报(?:名|活动)?|不去了|没空|去不了|来不了'
    other_refusal = bool(reported and re.search(refusal_words,reported[1])) or bool(re.search(r'(?:他|她)没空',question))
    purpose = ('acknowledge_refusal' if other_refusal or not delegated else 'draft_refusal') if scene == 'refuse' else 'reply'
    return {'scene': scene, 'mode': 'draft_for_user' if delegated else 'respond_to_user',
            'purpose':purpose,
            'instruction': ('只输出可直接发送的代写正文，不加引号、解释或“你可以说”。正文中的“我”指请求者，“你”指收信人；保留请求者明确给出的意愿和理由，不编造忙什么。'
                            if delegated else '直接回应当前说话者，不替对方改变决定，也不宣称现实中已见面或已办完任务。'),
            'refusal': (('本轮是在回应对方的拒绝，不是在写自己的拒绝。确认收到对方的决定，不能回答“我不参加”“不报了”，不替对方编没空的理由。' if purpose=='acknowledge_refusal'
                         else '本轮代写请求者自己的拒绝，拒绝者是请求者。保留明确的不想或没空，不编理由，不写成对方不参加。')
                        + '不对明确的“不想”添加“可能”。' if scene == 'refuse' else '')}
