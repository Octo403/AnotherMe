"""Apply and exercise the locally reviewed 2023 B-paper paragraph mappings.

The seven mappings were checked against the three pages of the accompanying
answer PDF (source 885). This is a specific reviewed sample, not a general
answer detector. Third-party text is written only to ignored runtime data.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from aitown.config import Settings
from aitown.graph import AnotherMeAgent
from aitown.question_bank.models import AnswerPair, BankConfig, COURSE_ID, GetArgs
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.search import QuestionBank


# number: (question paragraphs, answer paragraphs, source limitations)
REVIEW = {
    '一': ([35,37,41,45,49],[38,39,42,43,46,47,50,51],
        ['原资料第(1)(2)(4)小问只给结论和简要提示，没有完整论证']),
    '二': ([54],[55],[]),
    '三': ([59,60,61,62],[63,64],['原资料第(2)小问未写出最后的反证结论，按原文保留']),
    '四': ([67],[68],[]),
    '五': ([70,71],[72],['原资料仅有“数学归纳法”提示，没有完整证明']),
    '七': ([78],[79,80,81,82],[]),
    '八': ([87],[88],['原资料仅写“书上作业”，没有提供解答']),
}


def normalized(text):
    return ''.join(re.sub(r'[（(]\s*[)）]','',text).split())


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('data/question_bank'))
    parser.add_argument('--report',type=Path,default=Path('data/evaluation/question_bank/answers.json'))
    args=parser.parse_args()
    config=BankConfig(root=args.root)
    started=time.perf_counter()
    with BankRepository(config.database) as repo:
        answer_source=repo.source(774)
        header='\n'.join(r[0] for r in repo.conn.execute('SELECT text FROM pages WHERE source_id=? AND ordinal<=7 ORDER BY ordinal',(774,)))
        if '2023年2月25日' not in header or 'B卷' not in header or '集合与图论' not in header:
            raise ValueError('当前答案来源不是已核对的 B 卷')
        pages={r['ordinal']:r['text'] for r in repo.conn.execute('SELECT ordinal,text FROM pages WHERE source_id=?',(774,))}
        results,files=[],[]
        for source_id in (775,886):
            questions={q['number']:q for q in repo.rows(COURSE_ID) if q['source_id']==source_id}
            pairs=[]
            for number,(stem_locations,answer_locations,missing) in REVIEW.items():
                q=questions[number]
                stem='\n'.join(pages[n] for n in stem_locations)
                if normalized(q['text']) != normalized(stem):
                    raise ValueError(f'来源 {source_id} 第 {number} 题与核对过的答案版题干不一致')
                pairs.append(AnswerPair(question_number=number,question_text=q['text'],locations=answer_locations,
                    basis='Codex 对照答案 PDF 885 全部三页与 DOCX XML 段落核对：同课程、2023-02-25 B卷、相同原题号与完整题干；不评价解答正确率',
                    missing=missing))
            review_file=args.report.parent/f'b2023-pairs-{source_id}.json'
            review_file.parent.mkdir(parents=True,exist_ok=True)
            review_file.write_text(json.dumps([p.model_dump() for p in pairs],ensure_ascii=False,indent=2),encoding='utf-8')
            files.append(str(review_file))
            results.append(repo.pair_answers(source_id,774,pairs))
        paired=repo.conn.execute('SELECT count(*) FROM answers WHERE source_id=774').fetchone()[0]
        ids=[q['id'] for q in repo.rows(COURSE_ID) if q['source_id']==775 and q['number'] in REVIEW]
    bank=QuestionBank(config)
    hidden=bank.get(GetArgs(question_ids=ids)).model_dump()
    shown=bank.get(GetArgs(question_ids=ids,include_answer=True)).model_dump()
    assert all('answers' not in q for q in hidden['results'])
    assert all(q['answers'] and q['answers'][0]['path']==answer_source['path'] for q in shown['results'])
    assert all(q['answers'][0]['text'] not in json.dumps(hidden,ensure_ascii=False) for q in shown['results'])
    runtime=Path('.tmp/x3-answer-agent')
    agent=AnotherMeAgent(Settings(question_bank_dir=config.root,memory_db=runtime/'history.sqlite3',knowledge_dir=runtime/'knowledge'))
    try:
        session=f'answer-check-{time.time_ns()}'
        first=agent.ask('找集合与图论2023年期末关于幂集的证明题，先别给答案',session)
        candidates=first.skills[0].data['results']
        selected=next(n for n,q in enumerate(candidates,1) if q['answer_available'])
        assert all('answers' not in q for q in candidates)
        second=agent.ask(f'现在给我第{selected}题已有的答案和出处',session)
        answer=second.skills[0].data['results'][0]['answers'][0]
        assert answer['text'] in second.answer and '段落' in second.answer
        assert not second.model_calls and not second.errors
        assert agent.runtime_repository.conn.execute('SELECT count(*) FROM facts').fetchone()[0]==0
    finally:
        agent.close()
    report={'reviewer':'Codex','review_method':'manual visual comparison of PDF source 885 pages 1-3 with DOCX source 774 paragraphs; current question stems checked by exact normalized text',
        'results':results,'unique_question_numbers':7,'linked_records':paired,'partial_unique_answers':4,
        'unpaired':['六：题干有旧式嵌入公式，尚未完成数学条件审校；来源仅有“书上定理内容”提示'],
        'review_files':files,'checks':{'default_answers_hidden':True,'explicit_answers_have_source_locations':True,
        'agent_followup_returns_source_answer':True,'personal_facts_unchanged':True,'free_answer_model_calls':0},
        'seconds':time.perf_counter()-started,'get':shown,'agent_reply':second.answer}
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('get','agent_reply')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
