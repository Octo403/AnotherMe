"""Local diagnostic comparison. Does not write or invent subjective ratings."""
import json
import argparse
import sys
from pathlib import Path
from statistics import mean
from urllib.parse import urlparse

from aitown.config import Settings
from aitown.graph import AnotherMeAgent
from aitown.llm import CompatibleModel
from aitown.models import AnswerPayload
from aitown.storage import Repository
from aitown.style import ProfileStore, StyleRetriever, compose_prompt
from aitown.style.feedback import load_feedback, format_feedback_answer

CASES = [
    ('agree','下午一起去买点水果吗'), ('agree','我把复习提纲发给你看看行吗'),
    ('refuse','帮我回同学：周末的聚餐我不想参加，不要编理由'),
    ('refuse','对方说“我这次不参加了”，我该怎么回复他'),
    ('comfort','实验又出错了，折腾一晚上有点崩溃'), ('comfort','汇报时忘词了，现在还觉得很丢脸'),
    ('explain','怎么把一堆截图放进同一个文档'), ('explain','目录和摘要各有什么作用'),
    ('task','三个人一起做课程展示，先商量什么'), ('task','周五要提交报告，剩下两天怎么安排'),
    ('uncertain','你有资料能确认我以前做过志愿者吗'), ('uncertain','能确定我上学期住在哪栋楼吗'),
    ('refuse','有人找我帮忙，但我今晚要复习，替我写句拒绝的话'),
    ('refuse','我不想报名了'), ('refuse','同学说他没空帮我了，帮我回复一句'),
]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    settings=Settings.from_env(mode='live')
    if urlparse(settings.base_url).hostname not in ('localhost','127.0.0.1','::1'):
        raise ValueError('This private diagnostic requires the authorized local model')
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=Path('data/private/t6/repair_round1/verification'))
    output=parser.parse_args().output
    output.mkdir(parents=True,exist_ok=True)
    repo=Repository(settings.style_database)
    model=CompatibleModel(settings)
    profile=ProfileStore.read(settings.style_profile)
    claims=ProfileStore(repo).active(profile,'c0067')
    retriever=StyleRetriever(repo)
    records=[]
    try:
        for scene,question in CASES:
            hits=retriever.search(question,scope='c0067',scene=scene)
            feedback=load_feedback(settings.style_feedback,repo,scope='c0067',question=question,scene=scene)
            old=compose_prompt('你是 AnotherMe，用中文自然回应。',claims=claims,examples=hits)
            new=compose_prompt('你是 AnotherMe，用中文自然回应。',claims=claims,examples=hits,feedback=feedback,question=question,scene=scene)
            row={'scene':scene,'question':question,'style_ids':[h.id for h in hits],
                 'demonstration_ids':[d['style_id'] for d in feedback.get('demonstrations',[])],
                 'before_prompt':old,'after_prompt':new}
            for name,prompt in [('before',old),('after',new)]:
                row[name]=model.structured([{'role':'system','content':prompt},{'role':'user','content':question}],AnswerPayload).answer
                if name=='after':
                    row['after_raw']=row[name]
                    row[name]=format_feedback_answer(row[name],feedback)
            records.append(row)
            (output/'comparison.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:row[k] for k in ('scene','question','before','after')},ensure_ascii=False),flush=True)
    finally:
        model.close()
        repo.close()
    # Independent histories prevent before/after and case cross-contamination.
    settings.memory_db=output/'live_history.sqlite3'
    agent=AnotherMeAgent(settings)
    live=[]
    try:
        for i,(scene,question) in enumerate([CASES[0],CASES[2],CASES[4]]):
            reply=agent.ask(question,session=f'repair-{i}',scope='c0067',scene=scene)
            live.append(reply.model_dump(mode='json'))
    finally:agent.close()
    (output/'live_graph.json').write_text(json.dumps(live,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'model':settings.model,'local':True,'cases':len(records),'comparison_model_calls':2*len(records),
            'live_graph_cases':len(live),'subjective_ratings':None,
            'mean_chars':{k:round(mean(len(r[k]) for r in records),2) for k in ('before','after')},
            'terminal_full_stops':{k:sum(r[k].rstrip().endswith(('。','.')) for r in records) for k in ('before','after')},
            'understanding_cliche':{k:sum('希望你' in r[k] and '理解' in r[k] for r in records) for k in ('before','after')},
            'note':'Format counts are diagnostics, not naturalness or personal similarity scores'}
    (output/'diagnostics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
