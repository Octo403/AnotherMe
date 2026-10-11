import argparse
import json
import os
import sys
from pathlib import Path

from ..models import Reply, SkillCall
from ..runtime import RuntimeStore
from ..storage import Repository
from . import SkillContext, default_registry


def register_question_skills(registry, root=None):
    from dotenv import load_dotenv
    from ..question_bank.models import BankConfig
    from ..question_bank.search import QuestionBank
    from .question_bank import CourseStatus, QuestionGet, QuestionSearch
    load_dotenv()
    config = BankConfig(root=root or os.getenv('QUESTION_BANK_DIR','data/question_bank'),
        embedding_dir=os.getenv('LOCAL_EMBEDDING_DIR') or None,
        reranker_dir=os.getenv('LOCAL_RERANKER_DIR') or None,
        similar_provider=os.getenv('QUESTION_SIMILAR_PROVIDER','none'),
        similar_model=os.getenv('QUESTION_SIMILAR_MODEL',''))
    for skill in (QuestionSearch(QuestionBank(config)), QuestionGet(QuestionBank(config)), CourseStatus(QuestionBank(config))):
        registry.register(skill)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='独立运行 AnotherMe 业务技能')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    run = sub.add_parser('run')
    run.add_argument('name')
    run.add_argument('arguments', nargs='?', help='JSON 参数对象')
    run.add_argument('--arguments-file', type=Path, help='UTF-8 JSON 参数文件，避免 Windows 命令行引号歧义')
    run.add_argument('--database', type=Path)
    run.add_argument('--question-bank-root', type=Path)
    run.add_argument('--scope', default='')
    run.add_argument('--session', default='standalone-skills')
    run.add_argument('--enable-memory-correction', action='store_true')
    args = parser.parse_args()
    registry = default_registry(enable_correction=getattr(args, 'enable_memory_correction', False))
    register_question_skills(registry,getattr(args,'question_bank_root',None))
    if args.command == 'list':
        print(json.dumps(registry.catalog(include_disabled=True), ensure_ascii=False, indent=2))
        return
    if bool(args.arguments) == bool(args.arguments_file):
        parser.error('请提供 JSON 参数或 --arguments-file，二选一')
    try:
        raw = args.arguments_file.read_text(encoding='utf-8-sig') if args.arguments_file else args.arguments
        call = SkillCall(name=args.name, arguments=json.loads(raw))
    except (OSError, ValueError, TypeError) as exc:
        parser.error('技能参数必须为 JSON 对象：'+str(exc))
    if args.name in ('question_search','question_get','course_status'):
        result = registry.execute(call)
        print(result.model_dump_json(indent=2))
        if not result.ok:
            raise SystemExit(1)
        return
    if not args.database or not args.database.is_file():
        parser.error('该技能需要 --database 指定已有应用数据库')
    repo = Repository(args.database)
    runtime = RuntimeStore(repo)
    try:
        if args.name == 'memory_correction' and args.enable_memory_correction:
            # The caller explicitly provided this ID and value as CLI arguments.
            question = f'纠正事实 {call.arguments.get("fact_id", "")}：{call.arguments.get("value", "")}'
            request, _ = runtime.begin(args.session, 'anotherme', question, args.scope, None)
            result = runtime.execute_skill(registry, call, request, 0)
            runtime.complete(request, Reply(agent='AnotherMe', original_question=question, rewritten_question=question,
                answer='纠正已保存。' if result.ok else '纠正失败：'+str(result.error), intent='skill', mode='demo', skills=[result]), 'anotherme', 'demo')
        else:
            result = registry.execute(call, SkillContext(repo, scope=args.scope))
        print(result.model_dump_json(indent=2))
        if not result.ok:
            raise SystemExit(1)
    finally:
        runtime.close()


if __name__ == '__main__':
    main()
