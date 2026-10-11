"""Repair a rejected upload update only when the retained pages are unchanged."""
import argparse
import json
from pathlib import Path

import pymupdf as fitz

from aitown.question_bank.cleanup import cleanup_raw
from aitown.question_bank.models import BankConfig,COURSE_ID
from aitown.question_bank.repository import BankRepository

REJECTED='ValueError: MinerU 上传仅获准用于公开 FDU-Sharing 集合与图论原卷 PDF；此文件不在授权范围内'
RECOVERED='上次在线更新被上传授权范围拒绝；已核对公开副本逐页像素及原入库记录一致，保留既有处理结果。'


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--backup',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    config=BankConfig()
    approved=[];checks=[]
    with BankRepository(config.database,readonly=not args.apply) as repo,BankRepository(args.backup,readonly=True) as old:
        before=repo.coverage(COURSE_ID)
        for source in repo.sources(COURSE_ID):
            if source['error'] not in (REJECTED,RECOVERED):
                continue
            prior=old.source(source['id'])
            comparison={key:source[key]==prior[key] for key in ('course_id','repository','path','member','url','size','processed_at')}
            comparison['previous_result_valid']=prior['status'] in ('ready','partial')
            for table,columns in [('questions','*'),('pages','*'),('rendered_pages','*')]:
                query='SELECT '+columns+' FROM '+table+' WHERE source_id=? ORDER BY 1,2'
                comparison[table]=[tuple(r) for r in repo.conn.execute(query,(source['id'],))]==[tuple(r) for r in old.conn.execute(query,(source['id'],))]
            query='SELECT * FROM answers WHERE source_id=? OR question_id IN (SELECT id FROM questions WHERE source_id=?) ORDER BY id'
            comparison['answers']=[tuple(r) for r in repo.conn.execute(query,(source['id'],source['id']))]==[tuple(r) for r in old.conn.execute(query,(source['id'],source['id']))]
            pages=repo.conn.execute('SELECT * FROM pages WHERE source_id=? ORDER BY ordinal',(source['id'],)).fetchall()
            comparison['public_pdf_pages_equal_saved_images']=bool(pages)
            with fitz.open(source['local_path']) as pdf:
                comparison['page_count']=len(pdf)==len(pages)
                for page in pages:
                    saved=fitz.Pixmap(page['image'])
                    rendered=pdf[page['ordinal']-1].get_pixmap(matrix=fitz.Matrix(1.4,1.4))
                    if (saved.width,saved.height,saved.n,saved.samples)!=(rendered.width,rendered.height,rendered.n,rendered.samples):
                        comparison['public_pdf_pages_equal_saved_images']=False
            checks.append({'source_id':source['id'],'path':source['member'] or source['path'],'checks':comparison})
            if all(comparison.values()):approved.append((source,prior))
        if args.apply:
            with repo.conn:
                for source,prior in approved:
                    notes=json.loads(prior['notes'])+[RECOVERED]
                    repo.conn.execute('UPDATE sources SET status=?,error=NULL,notes=? WHERE id=? AND error=? AND processed_at=?',
                        (prior['status'],json.dumps(notes,ensure_ascii=False),source['id'],source['error'],source['processed_at']))
        after=repo.coverage(COURSE_ID)
    report={'applied':args.apply,'backup':str(args.backup),'checks':checks,'restorable_sources':[s['id'] for s,_ in approved],
            'coverage_before':before,'coverage_after':after,'note':'No reparse, no question or answer replacement, no uploads; pixel equality uses the retained registered full-page images.'}
    if args.apply:
        report['cleanup']=cleanup_raw(config,COURSE_ID)
        Path('data/evaluation/question_bank/mineru-rejected-recovery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
