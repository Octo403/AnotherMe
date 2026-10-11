"""Measure local OCR on the actual scanned PDFs; never index unaudited OCR."""
import json
import sys
import time
from pathlib import Path

from aitown.question_bank.models import BankConfig, COURSE_ID
from aitown.question_bank.prepare import parse_file
from aitown.question_bank.repository import BankRepository


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    config=BankConfig(pdf_ocr='windows')
    results=[]
    with BankRepository(config.database,readonly=True) as repo:
        for source_id,columns in ((785,1),(786,1),(879,2)):
            source=repo.source(source_id)
            started=time.perf_counter()
            output=config.root/'ocr_checks'/str(source_id)
            parsed=parse_file(Path(source['local_path']),source,output,config.model_copy(update={'ocr_columns':columns}))
            report={'source_id':source_id,'columns':columns,'seconds':time.perf_counter()-started,
                'pages':len(parsed.pages),'text_characters':sum(len(p.text) for p in parsed.pages),
                'indexed_questions':len(parsed.questions),'status':parsed.status,'notes':parsed.notes}
            (output/'parsed-ocr.json').write_text(parsed.model_dump_json(indent=2),encoding='utf-8')
            results.append(report)
            print(json.dumps(report,ensure_ascii=False),flush=True)
        coverage=repo.coverage(COURSE_ID)
    report={'provider':'Windows.Media.Ocr','language':'zh-Hans-CN','results':results,
        'assessment':'数学符号、指数与题目边界错误；OCR 页文字供人工审校，未审校题目不进入检索',
        'coverage':coverage}
    Path('data/evaluation/question_bank/ocr.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
