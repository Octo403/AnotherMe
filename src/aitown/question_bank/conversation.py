"""Resolve question ordinals only from this session's returned, still-live IDs."""
import json
import re

from ..models import Decision, SkillCall
from .models import COURSE_ID, COURSE_NAME, GetArgs


COURSE_MENTION = re.compile(r'(?<![\w@＠])[@＠]' + re.escape(COURSE_NAME) + r'(?=$|\s|[，,。:：;；？?!！])')


def mention_pattern(courses=None):
    names=[c['name'] for c in courses] if courses is not None else [COURSE_NAME]
    return re.compile(r'(?<![\w@＠])[@＠](?:'+'|'.join(re.escape(n) for n in sorted(names,key=len,reverse=True))+r')(?=$|\s|[，,。:：;；？?!！])')


def course_mention(text,courses=None):
    """Only an explicit user mention selects a registered course."""
    courses=courses if courses is not None else [{'id':COURSE_ID,'name':COURSE_NAME}]
    match=mention_pattern(courses).search(text)
    return next((c['id'] for c in courses if match and c['name']==match[0][1:]),'')


def named_course(text,courses):
    return next((c['id'] for c in sorted(courses,key=lambda c:len(c['name']),reverse=True)
                 if c['name'] in text or c['id'] in text),'')


def previous_result(agent, state):
    rows = agent.memory.rows(state['session'], state['agent'].id)
    for row in reversed(rows):
        run = agent.memory.conn.execute('SELECT reply_json FROM runtime_runs WHERE id=?', (row['request_id'],)).fetchone()
        if run and run[0]:
            reply = json.loads(run[0])
            tools = [r for r in reply.get('skills', []) if r['ok'] and r['name'] in ('question_search', 'question_get') and r['data'].get('results')]
            if tools:
                return reply, tools[-1]['data']['results']
            if (reply.get('decision') or {}).get('confidence_reason')=='课程找题需要补充条件':
                continue
            # An unrelated turn ends the active course context. Never revive an
            # old course query through a later personal conversation.
            if not any(c['name'] in ('question_search', 'question_get', 'course_status') for c in reply.get('tool_calls', [])):
                return None, []
            return reply, []
    return None, []


def previous_search_arguments(agent, state):
    """A selected question_get turn must retain the session's search filters."""
    for row in reversed(agent.memory.rows(state['session'], state['agent'].id)):
        run = agent.memory.conn.execute('SELECT reply_json FROM runtime_runs WHERE id=?', (row['request_id'],)).fetchone()
        if run and run[0]:
            reply = json.loads(run[0])
            for call in reversed(reply.get('tool_calls', [])):
                if call['name'] == 'question_search':
                    return call['arguments']
            if (reply.get('decision') or {}).get('confidence_reason')=='课程找题需要补充条件':
                continue
            if not any(c['name'] in ('question_search', 'question_get', 'course_status') for c in reply.get('tool_calls', [])):
                break
    return {}


def decision(agent, state):
    original = state['input']
    courses=agent.question_bank.courses()
    query = mention_pattern(courses).sub('', original).strip(' \t\n，,。:：;；')
    pinned_course=state.get('question_course') or course_mention(original,courses)
    pinned = bool(pinned_course)
    specific_course=named_course(query,courses)
    previous, items = previous_result(agent, state)
    previous_search = previous_search_arguments(agent,state) if previous else {}
    course=pinned_course or specific_course or previous_search.get('course') or (items[0]['course_id'] if items else '')
    course_name=next((c['name'] for c in courses if c['id']==course),COURSE_NAME)
    if previous_search.get('course') not in (None,course):
        previous_search={}
    items=[q for q in items if q['course_id']==course]
    def clarify(text):
        return Decision(intent='chat', query=query or course_name, original_question=original, needs_clarification=True,
            clarification_question=text,confidence_reason='课程找题需要补充条件')
    if pinned and not query:
        return clarify(f'已固定“{course_name}”题库。可以直接描述知识点，或补充年份、题型；点击输入框上方的 × 可取消固定。')
    specific = bool(specific_course)
    if re.search(r'微信|个人资料|记忆|我的(?:经历|偏好|计划|生日)|我(?:喜欢|不喜欢|去过|以前|昨天)|记住[：:]|记一下[：:]|纠正事实', query):
        return clarify(f'当前固定在“{course_name}”题库。个人资料请先点击输入框上方的 × 取消固定后再问。') if pinned else None
    wants = bool(re.search(r'找.*题|题库|知识库|准备.*资料|第[一二三四五六七八九十\d]+(?:道|个)?题|同类型|同类题|只看期末|只看期中|已有.{0,5}答案', query))
    fresh = specific and bool(re.search(r'找|题库',query)) and not re.search(
        r'再找|同类|只看|我说的是|我要的是|改成|换成|不限|取消',query)
    from .prepare import TOPICS
    course_terms = bool(re.search(r'题|期中|期末|(?:19|20)\d{2}',query)) or any(
        topic in query or any(word in query for word in words if len(word)>1) for topic,words in TOPICS.items())
    if course!=COURSE_ID and re.search('换成|改成|只要',query):
        course_terms=True
    refinement = bool(previous_search and not fresh and (re.search(
        r'证明题|计算题|判断题|选择题|构造题|不要.*题|简单一点|难一点|这些.*(?:难|简单)|不限年份|不限题型|不限考试|取消.*限制|再找|再来.*(?:题|道)|只看', query)
        or (course_terms and re.search(r'换成|改成|只要|我说的是|我要的是',query))))
    if not pinned and (not (wants or refinement) or not (specific or previous_search or items)):
        return None
    def choose(calls):
        return Decision(intent='skill', query=query, original_question=original, calls=calls, plan=['skill'],
            confidence='high', confidence_reason='明确课程找题或本会话实际题目追问；课程来源独立读取')
    if re.search('准备|进度|状态|更新|覆盖|新增|添加|加入|建立|整理|下载|获取.*(?:题库|知识库|资料)', query):
        return choose([SkillCall(name='course_status', arguments={'course': course})])
    ordinal = re.search(r'第([一二三四五六七八九十]|\d+)(?:道|个)?题', query)
    reference_item = None
    if ordinal:
        n = int(ordinal[1]) if ordinal[1].isdigit() else '一二三四五六七八九十'.index(ordinal[1])+1
        if n < 1 or n > len(items) or not items:
            return clarify('本会话还没有返回这么多道题，请先找题，再按结果顺序选择。')
        qid = items[n-1]['id']
        current = agent.question_bank.get(GetArgs(question_ids=[qid]))
        if not current.results:
            return clarify('此前题目来源已更新或移除，请重新找题。')
        if re.search('同类型|同类|相似题|类似.*题',query) and re.search('找|再来|筛选|推荐',query):
            reference_item=current.results[0]
        else:
            answer = bool(re.search('答案|解答|解析', query)) and not re.search('别.*答案|不要.*答案|不.*给.*答案|隐藏.*答案', query)
            return choose([SkillCall(name='question_get', arguments={'question_ids': [qid], 'include_answer': answer})])
    args = {'course': course, 'query': query}
    explicit = {}
    follow = reference_item is not None or (not fresh and (refinement or bool(re.search('再找|同类|只看|按.*本会话', query))))
    if follow and previous_search:
        args.update({k: v for k, v in previous_search.items() if k in ('year', 'exam', 'category', 'question_type')})
        args['query'] = previous_search.get('query') or ' '.join(items[0]['topics']) or items[0]['text']
        if re.search('同类型|同类|相似题|类似.*题', query) and items:
            if len(items) > 1 and reference_item is None:
                return clarify('请先选择哪一道题，再按它的知识点和题型继续找题。')
            reference=reference_item or items[0]
            args['query'] = ' '.join(reference['topics']) or previous_search.get('query') or reference['text'][:700]
            if reference['question_type']:
                args['question_type'] = reference['question_type']
            args['reference_ids'] = [reference['id']]
        if '再找' in query:
            args['exclude_ids'] = [q['id'] for q in items]
    years = list(re.finditer(r'(?:19|20)\d{2}', query))
    years = [year for year in years if not re.search(r'(?:不要|不是|排除)\s*$',query[:year.start()])]
    if years:
        explicit['year'] = int(years[-1][0])
    for term, exam in (('期末','final'), ('期中','midterm')):
        if term in query and not re.search(r'(?:不要|不是|排除)\s*'+term,query):
            explicit['exam'] = exam
    for qtype in ('证明', '计算', '判断', '选择', '构造'):
        if qtype+'题' in query and not re.search(r'(?:不要|不是|排除|别)\s*'+qtype+'题',query):
            explicit['question_type'] = qtype
    if '不限年份' in query:
        explicit['year'] = None
    if '不限题型' in query:
        explicit['question_type'] = None
    if '不限考试' in query:
        explicit['exam'] = None
    if follow:
        topic_change = re.search('换成|改成|只要',query) and (any(
            topic in query or any(word in query for word in words if len(word)>1) for topic,words in TOPICS.items())
            or (course!=COURSE_ID and bool(re.sub(r'换成|改成|只要|证明题|计算题|判断题|选择题|构造题|期中|期末|\d{4}年?|[\s，。；]', '',query))))
        args['query'] = query if topic_change else args['query']+'；本轮要求：'+query
    count = re.search(r'([一二两三四五六七八九十]|\d+)道', query)
    if count:
        explicit['limit'] = min(10, int(count[1]) if count[1].isdigit() else 2 if count[1] == '两' else '一二三四五六七八九十'.index(count[1])+1)
    if agent.question_bank.config.similar_provider == 'api':
        try:
            from .api_model import QuestionAPI
            plan = QuestionAPI(agent.question_bank.config.api, agent.question_bank.config.root/'processed').plan(
                query, previous_search if follow else {},course=course_name)
            if plan.clarification:
                return clarify(plan.clarification)
            # IDs and course ownership remain resolved locally, never by LLM.
            args.update(plan.model_dump(exclude={'clarification'}))
        except Exception as exc:
            state['warnings'].append(f'在线模型意图整理失败（{type(exc).__name__}），本轮使用本地找题规则。')
    args.update(explicit)
    return choose([SkillCall(name='question_search', arguments=args)])


def render_results(skills):
    parts = []
    for skill in skills:
        if not skill.ok or skill.name not in ('question_search', 'question_get', 'course_status'):
            continue
        data = skill.data
        if skill.name == 'question_search':
            calls = [c for c in data.get('model_calls', []) if c.get('status') == 'ok']
            if calls:
                parts.append('筛题模型：'+'、'.join(c.get('provider','')+' / '+c.get('model','') for c in calls)+'；题目来自本地题库。')
            else:
                parts.append('本轮为本地题库筛选。'+('仅按筛选条件列题，尚未比较具体知识点。' if data.get('method')=='filters' else ''))
        if data.get('warnings'):
            parts.append('；'.join(data['warnings']))
        if skill.name == 'course_status':
            coverage = data['coverage']
            parts.append(f"课程题库：{data['status']}；已发现 {coverage.get('discovered_files', 0)} 份文件，当前可检索 {coverage.get('questions', 0)} 题。")
            for job in data.get('jobs', [])[:1]:
                parts.append(f"准备任务 {job['id']}：{job['status']}，阶段 {job['stage']}，已完成 {len(job['done_ids'])}/{len(job['source_ids'])} 份。")
            continue
        if not data['results']:
            preparing=data.get('coverage',{}).get('preparation')
            if preparing:
                parts.append(f"课程资料正在准备，任务 {preparing['id']}：{preparing['stage']}，已完成 {preparing['done']}/{preparing['total']} 份。可以继续聊天，完成后再找题。")
                continue
            parts.append({'needs_preparation': '课程题库尚未准备，请从资料与设置启动课程准备。',
                'no_result': '已处理范围内没有匹配结果；这不表示历年没有考过。',
                'invalid_ids': '题目 ID 不存在或来源已更新，请重新找题。'}.get(data['status'], data['status']))
        for n, q in enumerate(data['results'], 1):
            locator = '段落' if (q['member'] or q['path']).endswith('.docx') else '页'
            year = str(q['year']) if q['year'] else '年份未确认'
            title = f"{n}. {year} · {q['exam'] or '考试类别未确认'} · {q['category']} · 原题号 {q['number']}"
            parts.append(title+'\n'+q['text']+'\n知识点：'+('、'.join(q['topics']) or '未标注')+
                '\n出处：'+q['path']+((' / '+q['member']) if q['member'] else '')+f"（{locator} "+'、'.join(map(str,q['pages']))+'）\n'+q['url'])
            if q.get('match_reason'):
                parts.append('匹配理由：'+q['match_reason'])
            parts.append('有来源答案。' if q.get('answer_available') else '未发现可确认配对的来源答案。')
            if q['missing']:
                parts.append('提取缺口：'+'；'.join(q['missing']))
            if 'answers' in q:
                for a in q['answers']:
                    locator={'paragraph':'段落','line':'行','page':'页'}.get(a.get('location_kind'),'位置')
                    parts.append('来源答案：'+a['text']+'\n答案出处：'+a['path']+
                        ((' / '+a['member']) if a['member'] else '')+'（'+locator+' '+
                        '、'.join(map(str,a['locations']))+'）\n'+a['url'])
                    if a.get('missing'):
                        parts.append('答案缺口：'+'；'.join(a['missing']))
                if not q['answers']:
                    parts.append('未发现可确认配对的来源答案。')
        if data.get('missing_ids'):
            parts.append('部分题目已失效，请重新检索。')
        coverage = data.get('coverage', {})
        if coverage.get('states') and any(s not in ('ready', 'skipped', 'expanded') for s in coverage['states']):
            parts.append('当前为部分覆盖；尚有未处理、不支持或提取不完整的资料。')
    return '\n\n'.join(parts)
