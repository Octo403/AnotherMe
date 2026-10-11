"""Compare two local extractors on an actual downloaded source, without changing IDs."""
import json
from pathlib import Path

from aitown.question_bank.models import BankConfig
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.prepare import parse_file


def main():
    config = BankConfig(mineru_python=Path('.venv-mineru/Scripts/python.exe'))
    report=[]
    with BankRepository(config.database,readonly=True) as repo:
        for sid in (773,775,776):
            source=repo.source(sid)
            path=Path(source['local_path'])
            parsed=parse_file(path,source,config.root/'extraction_check'/str(sid),config)
            report.append({'source_id':sid,'method':'basic','locations':len(parsed.pages),
                'location_kind':parsed.pages[0].location_kind if parsed.pages else None,'questions':len(parsed.questions),
                'year':parsed.year,'status':parsed.status,'notes':parsed.notes,
                'missing':sum(bool(q.missing) for q in parsed.questions)})
        source=repo.source(773)
        parsed=parse_file(Path(source['local_path']),source,config.root/'extraction_check'/'mineru-773',
            config.model_copy(update={'extractor':'mineru'}))
        report.append({'source_id':773,'method':'mineru-flash','pages':len(parsed.pages),'questions':len(parsed.questions),
            'year':parsed.year,'status':parsed.status,'notes':parsed.notes,
            'missing':sum(bool(q.missing) for q in parsed.questions)})
    target=Path('data/evaluation/question_bank/extraction.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
