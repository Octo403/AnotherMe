import json
import re
from datetime import datetime, timedelta, timezone

from ..models import CheckIssue
from .skill import numbers

PERSONAL_ASSERTION = re.compile(r'[我你](?:曾经|以前|去年|上个月)?(?:去过|去了|喜欢|不喜欢|住在|出生|毕业|参加过)')
UNKNOWN = re.compile(r'不知道|无法确认|不能确认|没(?:有)?查到|缺少依据|资料不足')


def known_gaps(state):
    return {part for result in state['skills'] if result.ok and result.name == 'dialogue_summary'
            for part in result.data.get('missing_parts', [])}


def unanswered_parts(payload, state):
    gaps = {re.sub(r'\s+', '', part) for part in known_gaps(state)}
    return [part for part in payload.missing_parts if re.sub(r'\s+', '', part) not in gaps]


def source_numbers(sources):
    dates = [datetime.fromtimestamp(s.timestamp, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
             for s in sources if s.timestamp is not None]
    return numbers('\n'.join(s.text for s in sources)+'\n'+'\n'.join(dates))


def filter_date_numbers(state):
    decision=state['decision']
    points=[getattr(decision,key,None) for key in ('as_of','time_from','time_to')]
    dates=[datetime.fromtimestamp(point,timezone(timedelta(hours=8))).strftime('%Y-%m-%d') for point in points if point is not None]
    return numbers('\n'.join(dates))


def clock_numbers(text,sources):
    clocks={(int(hour),int(minute)) for source in sources for hour,minute in re.findall(r'(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)',source.text)}
    supported=set()
    for period,hour,minute in re.findall(r'(上午|中午|下午|晚上)(\d{1,2})点(?:(\d{1,2})分)?',text):
        value=int(hour);minutes=int(minute or 0)
        actual=value+12 if period in ('下午','晚上') and value<12 else value
        if (actual,minutes) in clocks:supported|=numbers(hour+' '+(minute or ''))
    return supported


def personal_assertion_text(payload,sources):
    """Exclude exact, attributed peer quotations from the speaker's own claims.

    The named peer, claim subject and cited source must agree, and the quoted
    text must occur verbatim in that source. Other first-person text is retained.
    """
    answer=payload.answer
    for claim in payload.claims:
        if claim.subject!='other' or not claim.subject_id:continue
        alias=claim.subject_id.removeprefix('other:')
        cited=[sources[ref] for ref in claim.source_refs if ref in sources and sources[ref].subject==claim.subject_id]
        pattern=re.escape(alias)+r'(?:说|提到)[：:]\s*[“"「]?([^。\n”"」]+)'
        for match in reversed(list(re.finditer(pattern,answer))):
            quote=match[1]
            if any(quote in source.text for source in cited):
                start,end=match.span(1)
                answer=answer[:start]+answer[end:]
    return answer


def check(payload, state):
    sources = {source.ref: source for source in state['sources']}
    tools = state['skills']
    issues = []
    def issue(code, message):
        if not any(old.code == code and old.message == message for old in issues):
            issues.append(CheckIssue(code=code, message=message))
    references = [*payload.source_refs, *(ref for claim in payload.claims for ref in claim.source_refs)]
    references.extend(re.findall(r'\[([^\]\n]+#\d+)\]', payload.answer))
    references.extend(re.findall(r'(?<![\w])[a-zA-Z0-9_.:/-]+#\d+', payload.answer))
    for ref in references:
        if ref not in sources:
            issue('missing_reference', '回答引用不在本轮有效证据中：'+ref)
    personal = state.get('personal_query', False)
    if personal and not UNKNOWN.search(payload.answer) and not references:
        issue('personal_without_reference', '个人事实回答没有引用本轮来源。')
    if PERSONAL_ASSERTION.search(personal_assertion_text(payload,sources)) and not UNKNOWN.search(payload.answer):
        supported = any(claim.subject == 'self' and any(ref in sources and sources[ref].subject == 'self' for ref in claim.source_refs)
                        for claim in payload.claims)
        if not supported:
            issue('unsupported_personal_assertion', '第一/第二人称的经历或偏好没有本人证据支持。')
    for claim in payload.claims:
        cited = [sources[ref] for ref in claim.source_refs if ref in sources]
        successful = []
        for ref in claim.tool_refs:
            if ref < 0 or ref >= len(tools) or not tools[ref].ok:
                issue('invalid_tool_reference', '断言引用了不存在或失败的工具结果。')
            else:
                successful.append(tools[ref])
        actor = claim.subject_id if claim.subject == 'other' else claim.subject
        if claim.subject in ('self', 'other') and not any(s.subject == actor for s in cited):
            issue('subject_mismatch', '断言主体与引用来源不一致：'+str(actor))
        calculation = successful and any(r.name == 'calculator' for r in successful) and not re.search(r'[我你](?:计划|去|喜欢|住|出生|毕业|参加)', claim.text)
        if personal and claim.subject == 'general' and cited and not calculation:
            issue('personal_subject_missing', '个人资料断言必须标明实际主体。')
        if personal and claim.subject == 'task' and not calculation:
            issue('task_as_personal_evidence', '会话任务参数不能代替个人事实证据。')
        if claim.subject not in ('general', 'task') and cited:
            for target in re.findall(r'(?:去过|去了|住在|喜欢)(?:喝|吃)?([^，。；、？！?!\s]{1,25})', claim.text):
                # A comparison contains several objects, not one invented place
                # or drink called "茶而不是咖啡". Require each object to occur.
                target = target.rstrip('吗呢吧呀')
                supported = any(target in source.text for source in cited)
                objects = re.split(r'而不是|而非|但是|而|但|以及|和', target)
                if not supported and not all(obj and any(obj in source.text for source in cited) for obj in objects):
                    issue('unsupported_object', '经历或偏好中的具体对象不在所引来源中。')
        if cited or successful:
            known = source_numbers(cited) | filter_date_numbers(state) | clock_numbers(claim.text,cited) | numbers(json.dumps([tool.data for tool in successful], ensure_ascii=False))
            if successful:
                known |= numbers(state['input'])
            if not numbers(claim.text) <= known:
                calculators = [i for i, result in enumerate(tools) if result.ok and result.name == 'calculator']
                suggestion = '；实际 calculator 的 tool_refs 为 '+str(calculators)+'，请核对后引用实际运算结果' if calculators else ''
                issue('unsupported_number', '断言中的数字没有引用证据或成功工具结果支持'+suggestion+'。')
        if cited:
            for negative, predicate, target in re.findall(r'(没(?:有)?|不|未|从未)?(去过|去了|喜欢)(?:喝|吃)?([^，。；、？！?!\s]{1,25})', claim.text):
                if negative: continue
                target = re.split(r'而不是|而非|但是|而|但', target)[0].rstrip('吗呢吧呀了')
                for obj in re.split(r'以及|和',target):
                    if obj and all(re.search(r'(?:没(?:有)?|不|未|从未)'+re.escape(predicate)+r'(?:喝|吃)?[：: ]*'+re.escape(obj), source.text) for source in cited):
                        issue('negation_lost', '引用中对“'+obj+'”的否定被改写成肯定；问句中的错误前提不能覆盖来源。')
            if not re.search(r'没|不|取消|从未', claim.text) and all('取消' in source.text for source in cited):
                issue('negation_lost', '引用中的取消被改写成肯定。')
            negative_interest=r'(?:不(?:是)?(?:很|太|特别|怎么|那么|非常)?(?:感冒|感兴趣|有兴趣)|没(?:有)?(?:什么|太多)?兴趣)'
            interests = re.findall(r'对([^，。；？！?!\s]{1,35}?)('+negative_interest+r'|感兴趣|有兴趣)',claim.text)
            for obj, predicate in interests:
                if predicate not in ('感兴趣','有兴趣'):continue
                grams={obj[i:i+2] for i in range(len(obj)-1)}
                contradicted=[]
                for source in cited:
                    negatives=re.findall(r'对([^，。；？！?!\s]{1,35}?)'+negative_interest,source.text)
                    contradicted.append(any(grams & {value[i:i+2] for i in range(len(value)-1)} for value in negatives))
                if contradicted and all(contradicted):
                    issue('negation_lost','来源明确对该对象不感兴趣或不很感冒，不能因问句的肯定前提改成感兴趣。')
    answer = re.sub(r'\[[^\]\n]+#\d+\]', '', payload.answer)
    for ref in sources:
        answer = answer.replace(ref, '')
    if state['sources'] or tools:
        known = source_numbers(state['sources']) | clock_numbers(answer,state['sources']) | numbers(state['input']+'\n'+json.dumps([result.data for result in tools if result.ok], ensure_ascii=False))
        if not numbers(answer) <= known:
            issue('unsupported_number', '最终文本包含本轮问题、来源或工具未提供的数字。')
    if any(not result.ok for result in tools) and re.search(r'已(?:完成|执行|成功)|执行成功|计算成功', payload.answer) and not re.search(r'失败|未完成|没有执行|未执行', payload.answer):
        issue('failed_tool_as_success', '回答把失败调用写成执行成功。')
    if state['decision'].plan and 'skill' in state['decision'].plan:
        for result in tools:
            if result.ok and result.name == 'calculator' and isinstance(result.data, dict):
                value = result.data.get('value')
                if value is not None and numbers(str(value)) and not numbers(str(value)) <= numbers(answer):
                    issue('missing_tool_result', '回答遗漏成功计算的实际结果。')
    if known_gaps(state) and not re.search(r'缺失|压缩|未复核|不可读|省略', payload.answer):
        issue('undisclosed_missing_source', '清洗事件存在不可读或省略内容，回答应明确说明。')
    for part in unanswered_parts(payload, state):
        issue('missing_part', '草稿声明未回答的问题：'+part)
    return issues
