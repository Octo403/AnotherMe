import argparse
import json
import sys
from pathlib import Path

from .repository import Repository


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="AnotherMe 本地应用数据库")
    parser.add_argument("--database",type=Path,default=Path("data/runtime/application.sqlite3"))
    sub = parser.add_subparsers(dest="command",required=True)
    imp = sub.add_parser("import-t3")
    imp.add_argument("directory",type=Path)
    imp.add_argument("--batch-id")
    sub.add_parser("counts")
    source = sub.add_parser("sources")
    source.add_argument("kind",choices=["fact","trait","style","document"])
    source.add_argument("id",type=int)
    query = sub.add_parser("query")
    query.add_argument("kind",choices=["fact","trait","style","history"])
    query.add_argument("keyword")
    query.add_argument("--session",default="default")
    delete = sub.add_parser("delete-source")
    delete.add_argument("id",type=int)
    document = sub.add_parser("delete-document")
    document.add_argument("id",type=int)
    correction = sub.add_parser("correct-fact")
    correction.add_argument("id",type=int)
    correction.add_argument("value")
    correction.add_argument("--source",type=int,nargs="+",required=True)
    args = parser.parse_args()
    repo = Repository(args.database)
    try:
        if args.command == "import-t3":
            result = repo.import_t3(args.directory,args.batch_id)
        elif args.command == "counts":
            result = repo.counts()
        elif args.command == "sources":
            result = [r.model_dump() for r in repo.sources(args.kind,args.id)]
        elif args.command == "query":
            result = ([r.model_dump() for r in repo.query_history(args.session,args.keyword)]
                if args.kind=="history" else [r.model_dump() for r in getattr(repo,"query_"+{"fact":"facts","trait":"traits","style":"styles"}[args.kind])(args.keyword)])
        elif args.command == "delete-source":
            result = {"deleted":repo.delete_source(args.id),"message_id":args.id}
        elif args.command == "delete-document":
            repo.delete_document(args.id)
            result = {"document_id":args.id,"status":"deleted"}
        else:
            result = {"superseded_fact_id":args.id,"new_fact_id":repo.correct_fact(args.id,args.value,args.source)}
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
