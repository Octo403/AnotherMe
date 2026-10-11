"""Prepare a blind 30 x 3 comparison; never substitute Codex for user ratings."""
import json
import random
import shutil
from pathlib import Path
from statistics import mean

from aitown.models import AnswerPayload
from .prompt import compose_prompt
from .retrieval import StyleRetriever
from .profile import ProfileStore

CASES = {
    'agree': ['明天要不要一起去食堂试试新菜？','我整理好了笔记，你要一起看看吗？','晚点一起散步好不好？','可以一起商量下周的小组安排吗？','想邀请你一起看个轻松的电影，可以吗？'],
    'refuse': ['今晚我没空，帮我礼貌拒绝一起出门的邀请。','这周任务太多，我不方便接新活，该怎么说？','我不想参加这次聚会，帮我自然地拒绝。','现在抽不出时间帮忙，怎样拒绝又不显得冷淡？','我不想通宵赶这个活动，帮我表达拒绝。'],
    'comfort': ['今天练习搞砸了，我有点难过。','准备了很久还是没考好，有点沮丧。','作业一直做不出来，压力越来越大。','面试前特别紧张，担心说不出话。','一天忙下来累死了，感觉自己什么也没做好。'],
    'explain': ['Word里的文件怎么另存为PDF？','为什么整理任务时要分清先后顺序？','怎么把很长的会议笔记整理得更清楚？','可以解释一下草稿和最终稿的区别吗？','为什么一个问题不确定时要先问清楚？'],
    'task': ['小组作业的资料还没整理好，先从哪里开始？','我们准备组队，先确认哪些事情比较好？','会议记录要交了，帮我列出下一步。','文件太多，想按课程整理，有什么安排建议？','明天有讨论课，今晚准备资料怎么分步骤？'],
    'uncertain': ['你确定我去年参加过那个比赛吗？','你还记得我第一次去图书馆是哪一天吗？','能确定我现在最喜欢什么饮料吗？','你知道我之前提过的那位老师是谁吗？','没有相关资料，你有把握说我去过那个地方吗？'],
}


def prepare(repo, profile, directory, scope):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    claims=ProfileStore(repo).active(profile,scope)
    retriever=StyleRetriever(repo)
    trials=[]
    ratings=[]
    rng=random.Random(20261004)
    for scene, questions in CASES.items():
        for question in questions:
            case_id=f'E{len(ratings)+1:02}'
            hits=retriever.search(question,scope=scope,scene=scene)
            variants=[('plain',(),()),('profile',claims,()),('profile_style',claims,hits)]
            rng.shuffle(variants)
            answers=[]
            for label,(variant,persona,styles) in zip('ABC',variants):
                trial_id=case_id+'-'+label
                trials.append({'trial_id':trial_id,'variant':variant,'question':question,'scene':scene,'scope':scope,
                    'system':compose_prompt('你是 AnotherMe，用中文自然回应。',claims=persona,examples=styles),
                    'style_ids':[h.id for h in styles],'answer':None,'status':'awaiting_model'})
                answers.append({'id':trial_id,'answer':None,'naturalness':None,'similarity':None,'scene_fit':None,'comment':''})
            ratings.append({'case_id':case_id,'question':question,'answers':answers})
    (directory/'trials_private.json').write_text(json.dumps(trials,ensure_ascii=False,indent=2),encoding='utf-8')
    (directory/'ratings.json').write_text(json.dumps({'rater':None,'cases':ratings},ensure_ascii=False,indent=2),encoding='utf-8')
    shutil.copyfile(Path(__file__).with_name('review.html'),directory/'review.html')
    return {'cases':len(ratings),'trials':len(trials),'completed':0,'user_ratings':0,'scope':scope,
            'status':'awaiting_model_and_user','style_trials_with_hits':sum(bool(t['style_ids']) for t in trials if t['variant']=='profile_style')}


def generate(model, directory):
    """Caller supplies its configured, authorized provider. Save each success."""
    directory=Path(directory)
    trials=json.loads((directory/'trials_private.json').read_text(encoding='utf-8'))
    for trial in trials:
        if trial['status']=='generated':continue
        reply=model.structured([{'role':'system','content':trial['system']},{'role':'user','content':trial['question']}],AnswerPayload)
        trial['answer']=reply.answer
        trial['status']='generated'
        (directory/'trials_private.json').write_text(json.dumps(trials,ensure_ascii=False,indent=2),encoding='utf-8')
    ratings=json.loads((directory/'ratings.json').read_text(encoding='utf-8'))
    by_id={t['trial_id']:t for t in trials}
    for case in ratings['cases']:
        for answer in case['answers']:answer['answer']=by_id[answer['id']]['answer']
    (directory/'ratings.json').write_text(json.dumps(ratings,ensure_ascii=False,indent=2),encoding='utf-8')
    return len(trials)


def summarize(directory):
    directory=Path(directory)
    ratings=json.loads((directory/'ratings.json').read_text(encoding='utf-8'))
    trials=json.loads((directory/'trials_private.json').read_text(encoding='utf-8'))
    mapping={t['trial_id']:t for t in trials}
    scores={variant:[] for variant in ('plain','profile','profile_style')}
    comments=[]
    scored_cases={}
    scenes={}
    for case in ratings['cases']:
        for row in case['answers']:
            values=[row[k] for k in ('naturalness','similarity','scene_fit')]
            if all(v is None for v in values) and not row['comment'].strip():continue
            if not ratings['rater']:raise ValueError('Actual user rater is required')
            trial=mapping[row['id']]
            if trial['status']!='generated' or row['answer']!=trial['answer']:raise ValueError('Cannot score missing or different model output')
            if all(v is None for v in values):
                comments.append({'id':row['id'],'comment':row['comment'],'scored':False})
                continue
            if any(type(v)!=int or not 1<=v<=5 for v in values):raise ValueError('All three scores must be integer 1..5')
            if not row['comment'].strip():raise ValueError('Describe what sounds similar or different')
            scores[trial['variant']].append(values)
            scored_cases.setdefault(case['case_id'],{})[trial['variant']]=values
            scenes[trial['scene']]=scenes.get(trial['scene'],0)+1
            comments.append({'id':row['id'],'comment':row['comment'],'scored':True})
    paired=[rows for rows in scored_cases.values() if len(rows)==3]
    def means(rows):
        return dict(zip(('naturalness','similarity','scene_fit'),[round(mean(v),3) for v in zip(*rows)])) if rows else None
    expected={variant:sum(trial['variant']==variant for trial in trials) for variant in scores}
    return {'rater':ratings['rater'],'completed_ratings':sum(len(rows) for rows in scores.values()),
            'complete':bool(trials) and all(len(rows)==expected[key] for key,rows in scores.items()),'variants':{
                key:{'count':len(rows),'mean':means(rows)} for key,rows in scores.items()},
            'rated_cases':len(scored_cases),'scene_rating_counts':scenes,
            'paired_cases':len(paired),'paired_variants':{
                key:{'count':len(paired),'mean':means([rows[key] for rows in paired])} for key in scores},
            'comments':comments}
