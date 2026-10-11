import re
from datetime import datetime, timedelta, timezone

from ..models import ReferenceResolution

PEOPLE = re.compile(r'(?<![A-Za-z0-9_])([a-z][a-z0-9_]{0,12})(?![A-Za-z0-9_])(?=\s*(?:[\u4e00-\u9fff]|$))', re.I)
IGNORE = {'word', 'pdf', 'excel', 'json', 'md', 'txt', 'chat', 'skill', 'calculator', 'event_parser', 'hello', 'hi', 'thanks', 'robot', 'master'}
PERSON = re.compile(r'(?<!其)[他她](?:的)?')
EVENT = re.compile(r'那件事|这件事|之前说的(?:事)?|刚才说的(?:事)?|这个(?:活动|计划)|它|那里')


def people(text):
    aliases = [match for match in PEOPLE.findall(text) if match.lower() not in IGNORE]
    named = re.findall(r'(?:^|[，。；\s]|同学|朋友)([\u4e00-\u9fff]{2,3})(?=说|去|住|计划|周[一二三四五六日天])', text)
    return list(dict.fromkeys(aliases+[name for name in named if name not in ('昨天', '今天', '明天', '后天', '我们', '你们', '他们', '本人', '方案', '活动') and not name.startswith(('我','你'))]))


def events(text):
    named = re.findall(r'(?:名称(?:是|为|定为)|活动(?:叫|名为))[：: ]?([^。；，\n]{2,30})', text)
    if named:
        return named
    topics = re.findall(r'[\u4e00-\u9fff]{0,6}(?:活动室|图书馆|博物馆|读书会|分享会)', text)
    if topics:
        return [re.sub(r'^(?:请问|这个|那个|查询|查一下)', '', topic) for topic in topics]
    if re.search(r'什么|哪|多少|如何|怎么|[？?]', text):
        return []
    return re.findall(r'((?:没有|没|不|打算|计划)?(?:去|参加|预约|组织|报名|取消|准备)[^。；，\n]{2,35})', text)


def resolve(question, history, source_ids):
    """Only user turns in this session can supply referents; no alias merging."""
    user_turns = [(source_ids[i] if i < len(source_ids) else None, message['content'])
                  for i, message in enumerate(m for m in history if m['role'] == 'user')]
    query, resolutions, clarification = question, [], ''
    for expression, kind, extractor in [(PERSON.search(question), 'person', people), (EVENT.search(question), 'event', events)]:
        if not expression:
            continue
        candidates = {}
        for turn, text in user_turns:
            for candidate in extractor(text):
                candidates.setdefault(candidate, []).append((turn, text))
        explicit = people(question) if kind == 'person' else []
        selected = explicit[0] if len(explicit) == 1 else next(iter(candidates)) if len(candidates) == 1 else None
        evidence = candidates.get(selected, []) if selected else [pair for entries in candidates.values() for pair in entries]
        resolution = ReferenceResolution(kind=kind, expression=expression.group(), selected=selected,
            candidates=list(candidates), turn_ids=list(dict.fromkeys(turn for turn, _ in evidence if turn is not None)),
            evidence=list(dict.fromkeys(text for _, text in evidence)))
        resolutions.append(resolution)
        if selected:
            replacement = selected+('的' if expression.group().endswith('的') else '')
            query = query.replace(expression.group(), replacement)
        else:
            choices = '、'.join(list(candidates)[:4])
            clarification = f'你指的是{choices}中的哪一项？' if choices else '这句话指的是哪位对象或哪件事？请补充名称。'
    return query, resolutions, clarification


def retrieval_focus(question, references):
    """Use the resolved topic for a generic follow-up, preserving new objects."""
    selected=[reference.selected for reference in references if reference.kind=='event' and reference.selected]
    if not selected:return None
    remainder=re.sub(r'[\s，。？！,.?!]', '', EVENT.sub('',question))
    if re.fullmatch(r'(?:我)?(?:提过|说过)(?:什么|哪些)(?:相关)?(?:内容|事情)|(?:有|还有)?(?:什么|哪些)(?:细节|内容)|(?:我)?的?原话(?:是什么|呢)?',remainder):
        return '；'.join(selected)
    return None


def temporal_filters(question, started_at):
    now = datetime.fromtimestamp(started_at, timezone(timedelta(hours=8)))
    start = end = None
    if '上个月' in question:
        end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start = (end-timedelta(days=1)).replace(day=1)
    elif '去年' in question:
        start = now.replace(year=now.year-1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=now.year)
    elif '昨天' in question or '今天' in question:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if '昨天' in question:
            start -= timedelta(days=1)
        end = start+timedelta(days=1)
    result = {'time_from': int(start.timestamp()), 'time_to': int(end.timestamp())-1} if start else {}
    match = re.search(r'截至\s*(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?', question)
    if match:
        year, month, day = map(int, match.groups())
        point = datetime(year, month, day, tzinfo=now.tzinfo)+timedelta(days=1)
        result['as_of'] = int(point.timestamp())-1
    return result
