import argparse
import json
import sys
from pathlib import Path

from ..storage import Repository
from .factory import create_retriever
from .models import SearchFilter


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Local semantic FAISS retrieval and maintenance")
    parser.add_argument("--database", type=Path, default=Path("data/runtime/application.sqlite3"))
    parser.add_argument("--index-dir", type=Path, default=Path("data/runtime/retrieval"))
    parser.add_argument("--embedding", type=Path)
    parser.add_argument("--reranker", type=Path)
    parser.add_argument("--threshold", type=float, default=.45)
    parser.add_argument("--rerank-threshold", type=float, default=.01)
    sub = parser.add_subparsers(dest="command", required=True)
    refresh = sub.add_parser("refresh")
    refresh.add_argument("--purpose", choices=["general", "personal", "style"])
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--purpose", choices=["general", "personal", "style"], default="general")
    search.add_argument("--subject")
    search.add_argument("--scope")
    search.add_argument("--as-of", type=int)
    search.add_argument("--time-from", type=int)
    search.add_argument("--time-to", type=int)
    search.add_argument("--scene")
    search.add_argument("--no-rerank", action="store_true")
    imp = sub.add_parser("import")
    imp.add_argument("path", type=Path)
    delete = sub.add_parser("delete-document")
    delete.add_argument("id", type=int)
    args = parser.parse_args()
    repo = Repository(args.database)
    retriever = None
    try:
        retriever = create_retriever(repo, args.index_dir, args.embedding, args.reranker,
                                    threshold=args.threshold, rerank_threshold=args.rerank_threshold)
        if args.command == "search":
            filters = SearchFilter(purpose=args.purpose, subject=args.subject, scope=args.scope, as_of=args.as_of,
                                   time_from=args.time_from, time_to=args.time_to, scene=args.scene)
            result = retriever.search(args.query, filters, rerank=not args.no_rerank).model_dump()
        elif args.command == "refresh":
            result = retriever.refresh(args.purpose)
        elif args.command == "import":
            if args.path.suffix.lower() not in (".txt", ".md"):
                parser.error("Only TXT/MD documents are supported")
            did = repo.upsert_document(str(args.path.resolve()), args.path.read_text(encoding="utf-8-sig"))
            result = {"document_id": did, "refresh": retriever.refresh("general")}
        else:
            repo.delete_document(args.id)
            result = retriever.refresh("general")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if retriever:
            retriever.close()
        repo.close()


if __name__ == "__main__":
    main()
