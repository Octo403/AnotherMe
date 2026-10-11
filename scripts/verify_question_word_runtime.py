"""Real Word previews, deduplicated results and extracted-original HTTP checks."""
import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pymupdf

from aitown.question_bank.models import BankConfig, COURSE_ID
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.search import QuestionBank
from aitown.ui.server import make_server
from aitown.ui.service import UIService


def main():
    config=BankConfig()
    tables=('sources','questions','pages','rendered_pages','answers','question_duplicates','sqlite_sequence')
    def snapshot():
        with sqlite3.connect(config.database) as connection:
            return {t:connection.execute('SELECT * FROM '+t+' ORDER BY 1,2').fetchall() for t in tables}
    before=snapshot()
    initial=json.loads(Path('data/evaluation/question_bank/word-previews-initial-20261008.json').read_text(encoding='utf-8'))
    with sqlite3.connect(initial['backup']) as connection:
        connection.row_factory=sqlite3.Row
        original_questions=[dict(q) for q in connection.execute('SELECT * FROM questions')]
        original_answers=[tuple(a) for a in connection.execute('SELECT * FROM answers ORDER BY id')]
    with BankRepository(config.database,readonly=True) as repo:
        rows=repo.rows(COURSE_ID)
        questions=repo.get([q['id'] for q in rows])
        coverage=repo.coverage(COURSE_ID)
        originals={s['id']:s for s in repo.sources(COURSE_ID)}
        word_sources={s['id'] for s in originals.values() if (s['member'] or s['path']).lower().endswith(('.doc','.docx'))}
        word_occurrences=[q for q in repo._rows(COURSE_ID) if q['source_id'] in word_sources]
        b=next(q for q in questions if q['source_id']==775 and q['number']=='七')
        grouped=repo.get(b['occurrence_ids'])
        answer=repo.get([b['id']],True)[0]['answers']
        current={q['id']:dict(q) for q in repo.conn.execute('SELECT * FROM questions')}
        protected=('id','source_id','number','text','pages','subquestions','topics','question_type','missing')
        original_preserved=all(all(current[q['id']][key]==q[key] for key in protected) for q in original_questions)
        scan_preserved=all(all(current[q['id']][key]==value for key,value in q.items())
            for q in original_questions if q['source_id']==879)
        answers_preserved=original_answers==[tuple(a) for a in repo.conn.execute('SELECT * FROM answers ORDER BY id')]
    service=object.__new__(UIService)
    service.settings=SimpleNamespace(question_bank_dir=config.root)
    service.agent=SimpleNamespace(question_bank=QuestionBank(config))
    service.call=lambda method,*args:getattr(service,method)(*args)
    server=make_server(service,0)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    checks={}; images=pages=files=converted=remote=0
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False,timeout=30) as client:
            for question in questions:
                for i,path in enumerate(question['images']):
                    response=client.get('/api/question-image',params={'id':question['id'],'index':i})
                    response.raise_for_status()
                    assert response.content==Path(path).read_bytes()
                    with pymupdf.open(stream=response.content,filetype='png') as image:
                        assert image.page_count==1
                    images+=1
                for page in question['source_pages']:
                    if page['location_kind']=='page' and page['image']:
                        response=client.get('/api/question-page',params={'id':question['id'],'page':page['ordinal']})
                        response.raise_for_status()
                        assert response.content==Path(page['image']).read_bytes()
                        pages+=1
                for origin in question['origins']:
                    response=client.get('/api/question-source',params={'id':question['id'],'source':origin['source_id']})
                    if origin['file_kind']=='remote':
                        assert response.status_code==404 and origin['url'].startswith('https://')
                        remote+=1
                        continue
                    response.raise_for_status()
                    if origin['file_kind']=='converted_pdf':
                        assert response.content==(config.root/'processed'/str(origin['source_id'])/'document.pdf').read_bytes()
                        converted+=1
                    else:
                        assert response.content==Path(originals[origin['source_id']]['local_path']).read_bytes()
                        files+=1
                    assert "filename*=UTF-8''" in response.headers['content-disposition']
            checks['all_unique_questions_have_readable_images']=all(q['images'] for q in questions)
            checks['word_b_seventh_is_one_card_three_origins']=len(grouped)==1 and len(b['origins'])==3
            checks['word_page_six_not_paragraph_239']=b['pages']==[239] and b['source_pages'][0]['ordinal']==6 and b['image_previews'][0]['kind']=='crop'
            checks['existing_answers_remain_opt_in']=b['answer_available'] and 'answers' not in b
            checks['duplicate_answer_once_with_both_origins']=len(answer)==1 and len(answer[0]['origins'])==2
            checks['mixed_word_sources_not_searchable']=not {780,787,878}&{q['source_id'] for q in rows}
            checks['zip_and_rar_members_extracted_or_cleaned']=all(s.get('raw_deleted_at') or (s['local_path'] and Path(s['local_path']).is_file()
                and Path(s['local_path']).stat().st_size==s['size']) for s in originals.values() if s['member'])
        checks['bank_tables_unchanged_by_http']=before==snapshot()
        checks['preexisting_ids_stems_locations_and_answers_preserved']=original_preserved and answers_preserved and scan_preserved
        report={'coverage':coverage,'checks':checks,'passed':all(checks.values()),
            'unique_questions':len(questions),'images_http_checked':images,'original_pages_http_checked':pages,
            'original_files_http_checked':files,'word_question_occurrences':len(word_occurrences),
            'converted_pdfs_http_checked':converted,'remote_source_links_checked':remote,
            'word_occurrences_with_images':sum(bool(q['images']) for q in word_occurrences),
            'needs_review':[s['id'] for s in originals.values() if s['status']=='needs_review'],
            'preexisting_questions_checked':len(original_questions),'original_answer_links_checked':len(original_answers),
            'note':'真实公开题库逐图/原页/转换 PDF HTTP 字节检查；已清理原件使用在线来源入口，不代表全部数学题干或浏览器布局已人工审校。'}
        Path('data/evaluation/question_bank/word-runtime.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        assert report['passed']
    finally:
        server.shutdown();server.server_close();worker.join()


if __name__=='__main__':main()
