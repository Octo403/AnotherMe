import argparse
import json
import sys
import os

from dotenv import load_dotenv
from ..providers import ProviderConfig

from .jobs import create_job, run_job
from .models import AnswerPair, BankConfig, COURSE_NAME, GetArgs, ParsedSource, SearchArgs
from .repository import BankRepository
from .search import QuestionBank
from .sources import GithubSource


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='独立课程题库；不读取个人资料库')
    parser.add_argument('--root', default='data/question_bank')
    parser.add_argument('--extractor',choices=['basic','mineru','mineru_api'])
    parser.add_argument('--ocr',choices=['none','windows'])
    parser.add_argument('--ocr-columns',type=int,choices=[1,2])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('discover', 'prepare', 'complete-previews', 'cleanup', 'reparse', 'status', 'resume', 'search', 'get', 'worker', 'review', 'pair-answers', 'index'):
        p = sub.add_parser(name)
        p.add_argument('--course', default=COURSE_NAME)
        if name == 'prepare':
            p.add_argument('--max-files', type=int)
            p.add_argument('--source-id', type=int)
            p.add_argument('--update', action='store_true')
        if name == 'resume':
            p.add_argument('--job-id', type=int)
        if name in ('reparse','complete-previews'):
            p.add_argument('--source-id',type=int)
        if name == 'worker':
            p.add_argument('--job-id', type=int, required=True)
        if name == 'review':
            p.add_argument('--source-id', type=int, required=True)
            p.add_argument('--file', required=True, help='核对后的 ParsedSource JSON；重解析会覆盖本次修正')
        if name == 'pair-answers':
            p.add_argument('--question-source-id',type=int,required=True)
            p.add_argument('--answer-source-id',type=int,required=True)
            p.add_argument('--file',required=True,help='AnswerPair 数组 JSON；正文从答案实际位置读取')
        if name == 'search':
            p.add_argument('--query', required=True)
            p.add_argument('--year', type=int)
            p.add_argument('--exam', choices=['final', 'midterm'])
            p.add_argument('--question-type')
            p.add_argument('--category')
            p.add_argument('--limit', type=int, default=3)
            p.add_argument('--reference-ids',type=int,nargs='+',default=[])
        if name == 'get':
            p.add_argument('--ids', type=int, nargs='+', required=True)
            p.add_argument('--include-answer', action='store_true')
    args = parser.parse_args(argv)
    load_dotenv()
    config = BankConfig(root=args.root,embedding_dir=os.getenv('LOCAL_EMBEDDING_DIR') or None,
        reranker_dir=os.getenv('LOCAL_RERANKER_DIR') or None,
        similar_provider=os.getenv('QUESTION_SIMILAR_PROVIDER','none'),similar_model=os.getenv('QUESTION_SIMILAR_MODEL',''),
        similar_images=os.getenv('QUESTION_SIMILAR_IMAGES','true'),
        api=ProviderConfig(
            base_url=os.getenv('QUESTION_API_BASE_URL','https://api.deepseek.com'),
            provider=os.getenv('QUESTION_API_PROVIDER','deepseek'),model=os.getenv('QUESTION_API_MODEL',''),
            api_key=os.getenv('QUESTION_API_KEY',''),images=os.getenv('QUESTION_API_IMAGES','false')),
        extractor=args.extractor or os.getenv('QUESTION_EXTRACTOR','basic'),
        mineru_api_key=os.getenv('MINERU_API_KEY',''),mineru_api_model=os.getenv('MINERU_API_MODEL','vlm'),
        mineru_timeout=float(os.getenv('MINERU_TIMEOUT','900')),
        mineru_python=os.getenv('MINERU_PYTHON') or None,mineru_tier=os.getenv('MINERU_TIER','flash'),
        pdf_ocr=args.ocr or os.getenv('QUESTION_PDF_OCR','none'),
        ocr_columns=args.ocr_columns or int(os.getenv('QUESTION_OCR_COLUMNS','1')))
    bank = QuestionBank(config)
    try:
        if args.command == 'index':
            from .semantic import build_index
            result = build_index(config,course=args.course)
        elif args.command == 'cleanup':
            from .cleanup import cleanup_raw
            result=cleanup_raw(config,args.course)
        elif args.command == 'complete-previews':
            from .completion import complete_previews
            result = complete_previews(config,args.course,source_id=args.source_id,
                progress=lambda message:print(json.dumps(message,ensure_ascii=False),flush=True))
        elif args.command == 'reparse':
            from pathlib import Path
            from .prepare import parse_file
            from .archives import restore_source
            from .cleanup import cleanup_raw
            changed,failures=[],[]
            remote=GithubSource(config)
            with BankRepository(config.database) as repo:
                for source in repo.sources(args.course):
                    if source['remote_removed'] or (args.source_id and source['id']!=args.source_id):
                        continue
                    if Path(source['member'] or source['path']).suffix.lower() not in ('.pdf','.doc','.docx','.txt','.md'):
                        continue
                    try:
                        path=restore_source(repo,source,config,remote)
                        repo.set_source(source['id'],status='processing')
                        parsed=parse_file(path,source,config.root/'processed'/str(source['id']),config)
                        repo.replace_parsed(source['id'],parsed)
                        changed.append(source['id'])
                    except Exception as exc:
                        repo.set_source(source['id'],status='failed',error=f'{type(exc).__name__}: {exc}')
                        failures.append(source['id'])
                cleanup=cleanup_raw(config,args.course,keep_originals=True)
                result={'reparsed':changed,'failed':failures,'coverage':repo.coverage(args.course),'cleanup':cleanup,
                        'note':'旧题目 ID 已失效，请重新运行 index 并重新找题'}
            remote.close()
        elif args.command == 'discover':
            remote = GithubSource(config)
            try:
                courses, sources = remote.discover()
                with BankRepository(config.database) as repo:
                    repo.save_discovery(courses, sources)
                    result = {'courses': len(courses), 'all_files': len(sources), 'files': repo.sources(args.course)}
            finally:
                remote.close()
        elif args.command == 'review':
            from pathlib import Path
            parsed = ParsedSource.model_validate_json(Path(args.file).read_text(encoding='utf-8-sig'))
            with BankRepository(config.database) as repo:
                source = repo.source(args.source_id)
                if source['course_id'] != repo.course_id(args.course):
                    raise ValueError('来源不属于请求课程')
                repo.replace_parsed(args.source_id,parsed)
            result = {'source_id':args.source_id,'questions':len(parsed.questions),'status':parsed.status}
            from .cleanup import cleanup_raw
            result['cleanup']=cleanup_raw(config,args.course,keep_originals=True)
        elif args.command == 'pair-answers':
            from pathlib import Path
            from pydantic import TypeAdapter
            pairs = TypeAdapter(list[AnswerPair]).validate_json(Path(args.file).read_text(encoding='utf-8-sig'))
            with BankRepository(config.database) as repo:
                if any(repo.source(sid)['course_id'] != repo.course_id(args.course)
                       for sid in (args.question_source_id,args.answer_source_id)):
                    raise ValueError('来源不属于请求课程')
                result = repo.pair_answers(args.question_source_id,args.answer_source_id,pairs)
        elif args.command == 'worker':
            result = run_job(config,args.job_id,progress=lambda message: print(message,flush=True),build_vectors=True)
        elif args.command in ('prepare', 'resume'):
            with BankRepository(config.database) as repo:
                if args.command == 'prepare':
                    if args.max_files is not None and args.max_files < 1:
                        raise ValueError('--max-files 必须至少为 1')
                    job_id = create_job(repo, args.course, max_files=args.max_files, source_id=args.source_id, update=args.update)
                else:
                    row = repo.conn.execute('SELECT id FROM jobs WHERE course_id=? ORDER BY id DESC LIMIT 1', (repo.course_id(args.course),)).fetchone()
                    job_id = args.job_id or (row[0] if row else None)
                    if job_id is None:
                        raise ValueError('没有可续跑的准备任务')
            result = run_job(config, job_id, resume=args.command == 'resume', progress=lambda message: print(message, flush=True))
        elif args.command == 'search':
            result = bank.search(SearchArgs(course=args.course, query=args.query, year=args.year, exam=args.exam,
                question_type=args.question_type, category=args.category, limit=args.limit,reference_ids=args.reference_ids)).model_dump()
        elif args.command == 'get':
            result = bank.get(GetArgs(question_ids=args.ids, include_answer=args.include_answer)).model_dump()
        else:
            result = bank.status(args.course).model_dump()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
