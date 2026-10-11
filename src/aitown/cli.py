import argparse
import sys
import json
import sqlite3
from pathlib import Path

from .config import Settings
from pydantic import ValidationError

from .graph import AnotherMeAgent
from .runtime import UserStatement
from .runtime.checkpoints import TurnPaused
from .errors import AgentError
from .llm import CompatibleModel


def main():
    # Use UTF-8 for Chinese JSON even when Windows pipes default to a legacy code page.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="AnotherMe 单 Agent LangGraph CLI")
    parser.add_argument("--mode", choices=["demo", "live"])
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--session", default="default")
    ask.add_argument("--scope",help="explicit cleaned conversation scope for style; no automatic identity guessing")
    ask.add_argument('--pause-after', choices=['context', 'intent', 'rag', 'skill'])
    chat = sub.add_parser("chat")
    chat.add_argument("--session", default="default")
    chat.add_argument("--scope")
    sub.add_parser("index")
    sub.add_parser("graph")
    resume = sub.add_parser('resume')
    resume.add_argument('--session', default='default')
    history = sub.add_parser('history')
    history.add_argument('--session', default='default')
    source = sub.add_parser('history-source')
    source.add_argument('id', type=int)
    source.add_argument('--session', default='default')
    sub.add_parser('cleanup-context')
    remember = sub.add_parser('remember')
    remember.add_argument('attribute')
    remember.add_argument('value')
    remember.add_argument('--scope', required=True)
    remember.add_argument('--session', default='default')
    correct = sub.add_parser('correct')
    correct.add_argument('fact_id', type=int)
    correct.add_argument('value')
    correct.add_argument('--scope', required=True)
    correct.add_argument('--session', default='default')
    provider = sub.add_parser('provider-test')
    provider.add_argument('--output', type=Path)
    ui=sub.add_parser('ui',help='本地浏览器界面，默认独立合成资料')
    ui.add_argument('--port',type=int,default=8501)
    ui.add_argument('--data',choices=['synthetic','configured','wechat'],default='synthetic')
    ui.add_argument('--directory',type=Path)
    ui.add_argument('--scope',default='')
    args = parser.parse_args()
    try:
        settings = Settings.from_env(mode=(args.mode or 'demo') if args.command=='ui' else args.mode)
    except ValidationError as exc:
        # Do not echo validation inputs, which may contain credentials.
        errors = [f"{'.'.join(str(part) for part in error['loc']) or 'settings'}: {error['msg']}"
                  for error in exc.errors(include_input=False, include_url=False)]
        parser.error("配置错误：" + "；".join(errors))
    if args.command == 'provider-test':
        if settings.mode != 'live':
            parser.error('provider-test 需要 --mode live，使用合成数据测量实际模型服务')
        model = CompatibleModel(settings)
        try:
            report = model.probe()
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            model.close()
        return
    if args.command=='ui':
        from .ui import serve
        serve(settings,port=args.port,directory=args.directory or Path('data/runtime/ui-'+args.data),
              data=args.data,mode=args.mode or 'demo',scope=args.scope)
        return
    try:
        agent = AnotherMeAgent(settings)
    except sqlite3.Error as exc:
        info = AnotherMeAgent.database_error(exc, 'startup').info
        print(json.dumps({'status': 'failed', 'error': info.model_dump(), 'recovery': '修复数据库或检查点路径后重启。'}, ensure_ascii=False))
        raise SystemExit(1)
    try:
        if args.command == "ask":
            print(agent.ask(args.question, args.session, scope=args.scope,
                           interrupt_after=[args.pause_after] if args.pause_after else None).model_dump_json(indent=2))
        elif args.command == "chat":
            print("输入 /exit 退出。", flush=True)
            while True:
                try:
                    question = input("你 > ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if question == "/exit":
                    break
                if question:
                    reply = agent.resume(args.session) if question == '/resume' else agent.ask(question, args.session, scope=args.scope)
                    print(reply.answer)
                    print("流程：" + " → ".join(reply.trace))
                    for warning in reply.warnings:
                        print("提示：" + warning)
        elif args.command == "index":
            if agent.retriever:
                print(json.dumps(agent.retriever.refresh(), ensure_ascii=False, indent=2))
            else:
                print(f"已构建演示 FAISS 索引：{len(agent.knowledge.chunks)} 个片段。")
        elif args.command == "graph":
            print(agent.graph.get_graph().draw_mermaid())
        elif args.command == 'resume':
            print(agent.resume(args.session).model_dump_json(indent=2))
        elif args.command == 'history':
            print(json.dumps(agent.memory.history_bundle(args.session, agent.settings.agent.id), ensure_ascii=False, indent=2))
        elif args.command == 'history-source':
            print(json.dumps(agent.memory.source_turn(args.id, args.session, agent.settings.agent.id), ensure_ascii=False, indent=2))
        elif args.command == 'cleanup-context':
            print(json.dumps(agent.cleanup_context(), ensure_ascii=False, indent=2))
        elif args.command in ('remember', 'correct'):
            attribute = args.attribute if args.command == 'remember' else agent.memory.conn.execute('SELECT attribute FROM facts WHERE id=?', (args.fact_id,)).fetchone()
            if attribute is None:
                raise ValueError('事实不存在')
            if args.command == 'correct':
                attribute = attribute[0]
            statement = UserStatement(kind='user_correction' if args.command == 'correct' else 'user_statement',
                attribute=attribute, value=args.value, scope=args.scope,
                corrects_fact_id=args.fact_id if args.command == 'correct' else None)
            fid = agent.memory.record_statement(statement, f'用户明确{args.command}：{attribute}={args.value}',
                args.session, agent.settings.agent.id)
            print(json.dumps({'fact_id': fid, 'sources': [s.model_dump() for s in agent.runtime_repository.sources('fact', fid)]}, ensure_ascii=False, indent=2))
    except TurnPaused as exc:
        print(json.dumps({'status': 'paused', 'request_id': exc.request_id, 'next': exc.next_nodes}, ensure_ascii=False))
    except AgentError as exc:
        print(json.dumps({'status': 'failed', 'error': exc.info.model_dump(), 'recovery': '修复原因后使用 resume；已提交写入不会重做。'}, ensure_ascii=False))
        raise SystemExit(1)
    except ValueError as exc:
        parser.error(str(exc))
    finally:
        agent.close()


if __name__ == "__main__":
    main()
