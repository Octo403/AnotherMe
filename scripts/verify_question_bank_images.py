"""Measure current question/image correspondence and original-page HTTP access."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pymupdf as fitz

from aitown.question_bank.models import BankConfig, COURSE_ID
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.search import QuestionBank
from aitown.ui.server import make_server
from aitown.ui.service import UIService


def main():
    config=BankConfig()
    with BankRepository(config.database) as repo:  # Ordinary additive schema migration only.
        questions=repo.get([q['id'] for q in repo.rows(COURSE_ID)])
    service=object.__new__(UIService)
    service.settings=SimpleNamespace(question_bank_dir=config.root)
    service.agent=SimpleNamespace(question_bank=QuestionBank(config))
    service.call=lambda method,*args:getattr(service,method)(*args)
    server=make_server(service,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    checks=[]
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False) as client:
            for q in questions:
                for preview in q['image_previews']:
                    response=client.get(f'/api/question-image?id={q["id"]}&index={preview["index"]}')
                    response.raise_for_status()
                    with fitz.open(stream=response.content,filetype=response.headers['content-type'].split('/')[-1]) as image:
                        readable=image.page_count==1
                    checks.append({'question_id':q['id'],**preview,'http_ok':True,'readable':readable})
                for page in q['source_pages']:
                    if page['image'] and page['location_kind']=='page':
                        response=client.get(f'/api/question-page?id={q["id"]}&page={page["ordinal"]}')
                        response.raise_for_status()
                        assert response.content==Path(page['image']).read_bytes()
        report={'questions':len(questions),'images':len(checks),
            'located_images':sum(c['page'] is not None for c in checks),
            'kinds':{kind:sum(c['kind']==kind for c in checks) for kind in sorted({c['kind'] for c in checks})},
            'checks':checks,'passed':all(c['http_ok'] and c['readable'] for c in checks),
            'note':'实际 HTTP 与原页字节对应检查；不代替逐题裁剪内容和浏览器视觉审校'}
        target=Path('data/evaluation/question_bank/images.json');target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='checks'},ensure_ascii=False))
    finally:
        server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
