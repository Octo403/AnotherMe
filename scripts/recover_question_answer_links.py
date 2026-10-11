"""Reattach reviewed answers after an update only when stems and answer text agree."""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from aitown.question_bank.models import AnswerPair,BankConfig
from aitown.question_bank.repository import BankRepository


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--backup',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();config=BankConfig();groups=defaultdict(list);checks=[]
    with BankRepository(config.database,readonly=not args.apply) as repo,BankRepository(args.backup,readonly=True) as old:
        before=[tuple(r) for r in repo.conn.execute('SELECT * FROM questions ORDER BY id')]
        for answer in old.conn.execute('SELECT * FROM answers ORDER BY id'):
            prior=old.conn.execute('SELECT * FROM questions WHERE id=?',(answer['question_id'],)).fetchone()
            current=repo.conn.execute('SELECT * FROM questions WHERE source_id=? AND number=?',(prior['source_id'],prior['number'])).fetchone()
            source=repo.source(prior['source_id']);original_source=old.source(prior['source_id'])
            answer_source=repo.source(answer['source_id']);old_answer_source=old.source(answer['source_id'])
            check={'source_id':prior['source_id'],'answer_source_id':answer['source_id'],'number':prior['number'],
                   'same_stem':bool(current and current['text']==prior['text']),
                   'same_sources':all(source[k]==original_source[k] and answer_source[k]==old_answer_source[k]
                                      for k in ('course_id','repository','path','member','url','year','exam','category'))}
            pages=[repo.conn.execute('SELECT text FROM pages WHERE source_id=? AND ordinal=?',(answer['source_id'],n)).fetchone()
                   for n in json.loads(answer['locations'])]
            check['same_answer_at_same_locations']=all(pages) and '\n'.join(p['text'] for p in pages)==answer['text']
            checks.append(check)
            if all(check[k] for k in ('same_stem','same_sources','same_answer_at_same_locations')):
                groups[(prior['source_id'],answer['source_id'])].append(AnswerPair(question_number=current['number'],
                    question_text=current['text'],locations=json.loads(answer['locations']),basis=answer['basis'],missing=json.loads(answer['missing'])))
        results=[]
        if args.apply:
            for (question_source,answer_source),pairs in groups.items():
                results.append(repo.pair_answers(question_source,answer_source,pairs))
        assert before==[tuple(r) for r in repo.conn.execute('SELECT * FROM questions ORDER BY id')]
    report={'applied':args.apply,'checks':checks,'matching_links':sum(map(len,groups.values())),'results':results,
            'question_records_unchanged':True,'note':'Current IDs used only for byte-for-byte equal stems and actual answer paragraphs at the same locations.'}
    if args.apply:Path('data/evaluation/question_bank/answer-links-recovery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
