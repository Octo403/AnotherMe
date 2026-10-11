"""Apply the requested raw-cache cleanup and measure retained public-bank data."""
import json
import sqlite3
from pathlib import Path

from aitown.question_bank.cleanup import cleanup_raw
from aitown.question_bank.models import BankConfig,COURSE_ID
from aitown.question_bank.repository import BankRepository


def main():
    config=BankConfig()
    def size(directory):
        return sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())
    def snapshot():
        with sqlite3.connect(config.database) as connection:
            return {t:connection.execute('SELECT * FROM '+t+' ORDER BY 1,2').fetchall()
                    for t in ('questions','pages','rendered_pages','answers','sqlite_sequence')}
    before=snapshot()
    with BankRepository(config.database,readonly=True) as repo:
        groups={q['id']:q['occurrence_ids'] for q in repo.rows(COURSE_ID)}
        sources=repo.sources(COURSE_ID)
    original_size=size(config.root)
    raw_size=size(config.root/'raw')
    result=cleanup_raw(config,COURSE_ID)
    with BankRepository(config.database,readonly=True) as repo:
        current=repo.sources(COURSE_ID)
        after_groups={q['id']:q['occurrence_ids'] for q in repo.rows(COURSE_ID)}
        metadata=lambda rows:[{k:v for k,v in s.items() if k not in ('local_path','raw_deleted_at')} for s in rows]
        checks={
            'question_pages_ids_and_answers_unchanged':before==snapshot(),
            'duplicate_groups_unchanged':groups==after_groups,
            'all_source_provenance_unchanged':metadata(sources)==metadata(current),
            'raw_byte_reduction_matches_report':raw_size-size(config.root/'raw')==result['bytes_freed'],
            'retained_raw_files_exist':all(Path(s['local_path']).is_file() for s in current if s['local_path']),
            'cleaned_sources_do_not_advertise_raw_files':all(repo.source_file_kind(s['id'])!='raw' for s in current if s.get('raw_deleted_at')),
            'no_delete_failures':not result['failed']}
        report={'checks':checks,'passed':all(checks.values()),'cleanup':result,
                'coverage':repo.coverage(COURSE_ID),'bank_bytes_before':original_size,'bank_bytes_after':size(config.root),
                'raw_bytes_before':raw_size,'raw_bytes_after':size(config.root/'raw'),
                'retained':[{'id':s['id'],'file':s['member'] or s['path'],'status':s['status']} for s in current if s['local_path']]}
    target=Path('data/evaluation/question_bank/raw-cleanup.json')
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('retained','cleanup')},ensure_ascii=False,indent=2))
    print(json.dumps({'deleted':len(result['deleted']),'retained':len(result['retained']),'bytes_freed':result['bytes_freed']},ensure_ascii=False))
    assert report['passed']


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
