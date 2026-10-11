"""Apply and measure the local public-bank Word/archive completion operation."""
import json
import sqlite3
import time
import argparse
from pathlib import Path

from aitown.question_bank.completion import complete_previews
from aitown.question_bank.models import BankConfig, COURSE_ID
from aitown.question_bank.repository import BankRepository


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-id',type=int,action='append')
    args=parser.parse_args()
    config=BankConfig()
    database=config.database
    with sqlite3.connect(database) as connection:
        connection.row_factory=sqlite3.Row
        before_questions=[dict(r) for r in connection.execute('SELECT * FROM questions ORDER BY id')]
        before_answers=[tuple(r) for r in connection.execute('SELECT * FROM answers ORDER BY id')]
        originals={Path(r['local_path']):Path(r['local_path']).read_bytes()
            for r in connection.execute('SELECT local_path FROM sources WHERE local_path IS NOT NULL')
            if Path(r['local_path']).is_file()}
        backup=config.root/'backups'/time.strftime('before-word-completion-%Y%m%d-%H%M%S.sqlite3')
        backup.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(backup) as destination:
            connection.backup(destination)
    runs=[complete_previews(config,COURSE_ID,source_id=sid,
        progress=lambda value:print(json.dumps(value,ensure_ascii=False),flush=True)) for sid in (args.source_id or [None])]
    result={key:[item for run in runs for item in run[key]] for key in ('completed','archives','failed')}
    result['cleanup']=[run['cleanup'] for run in runs]
    result['coverage']=runs[-1]['coverage']
    with BankRepository(database,readonly=True) as repo:
        after={r['id']:dict(r) for r in repo.conn.execute('SELECT * FROM questions')}
        protected=('id','source_id','number','text','pages','subquestions','topics','question_type','missing')
        existing_preserved=all(all(after[q['id']][key]==q[key] for key in protected) for q in before_questions)
        answers_preserved=before_answers==[tuple(r) for r in repo.conn.execute('SELECT * FROM answers ORDER BY id')]
        reviewed_preserved=all(all(after[q['id']].get(key)==value for key,value in q.items())
            for q in before_questions if q['source_id']==879)
        sources=repo.sources(COURSE_ID)
        word=[s for s in sources if (s['member'] or s['path']).lower().endswith(('.doc','.docx'))]
        word_questions=[q for q in after.values() if q['source_id'] in {s['id'] for s in word if s['status'] in ('ready','partial')}]
        archive_members=[s for s in sources if s['member'] and not s['remote_removed']]
        cleaned={item['source_id'] for run in runs for item in run['cleanup']['deleted']}
        with sqlite3.connect(backup) as previous:
            cleaned_paths={Path(path) for sid,path in previous.execute('SELECT id,local_path FROM sources WHERE local_path IS NOT NULL') if sid in cleaned}
        result.update(backup=str(backup),original_files_unchanged_or_cleaned=all(p.read_bytes()==content if p.exists() else p in cleaned_paths for p,content in originals.items()),
            existing_ids_stems_locations_preserved=existing_preserved,existing_answers_preserved=answers_preserved,
            reviewed_scan_preserved=reviewed_preserved,answer_links=len(before_answers),
            word_question_occurrences=len(word_questions),word_occurrences_with_images=sum(bool(json.loads(q['images'])) for q in word_questions),
            needs_review=[{'source_id':s['id'],'path':s['member'] or s['path']} for s in sources if s['status']=='needs_review'],
            word_without_images=[{'id':q['id'],'source_id':q['source_id'],'number':q['number']} for q in word_questions if not json.loads(q['images'])],
            all_archive_members_extracted=all(s.get('raw_deleted_at') or (s['local_path'] and Path(s['local_path']).is_file()
                and Path(s['local_path']).stat().st_size==s['size']) for s in archive_members),
            duplicate_groups=[{'id':q['id'],'occurrences':q['occurrence_ids']} for q in repo.rows(COURSE_ID) if len(q['occurrence_ids'])>1])
    target=Path('data/evaluation/question_bank/word-previews-20261008.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    assert existing_preserved and answers_preserved and reviewed_preserved and result['original_files_unchanged_or_cleaned']
    assert result['all_archive_members_extracted']


if __name__=='__main__':
    main()
