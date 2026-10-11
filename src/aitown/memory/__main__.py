import argparse
import json
import sys
from pathlib import Path

from aitown.storage import Repository
from .extract import extract_batch
from .repository import MemoryStore


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Local memory extraction and reviewed export")
    parser.add_argument("--database", type=Path, default=Path("data/runtime/application.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("extract")
    draft.add_argument("--limit", type=int)
    draft.add_argument("--output", type=Path, required=True)
    load = commands.add_parser("load")
    load.add_argument("input", type=Path)
    load.add_argument("--reviews", type=Path)
    export = commands.add_parser("export")
    export.add_argument("output", type=Path)
    query = commands.add_parser("query")
    query.add_argument("keyword", nargs="?", default="")
    query.add_argument("--subject", default="self")
    query.add_argument("--category")
    query.add_argument("--as-of", type=int)
    query.add_argument("--basis", choices=("direct", "inferred"), default="direct")
    query.add_argument("--candidates", action="store_true")
    args = parser.parse_args()
    repo = Repository(args.database)
    try:
        store = MemoryStore(repo)
        if args.command == "extract":
            entries = extract_batch(repo, args.limit)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text("".join(e.model_dump_json() + "\n" for e in entries), encoding="utf-8")
            print(json.dumps({"entries": len(entries), "output": str(args.output)}))
        elif args.command == "load":
            from .models import Memory
            entries = [Memory.model_validate_json(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
            reviews = json.loads(args.reviews.read_text(encoding="utf-8")) if args.reviews else {}
            print(json.dumps({"added_or_updated": store.save(entries, reviews)}))
        elif args.command == "export":
            print(json.dumps(store.export(args.output), ensure_ascii=False))
        else:
            print(json.dumps([m.model_dump() for m in store.query(args.keyword, subject=args.subject, category=args.category, as_of=args.as_of, basis=args.basis, include_candidates=args.candidates)], ensure_ascii=False))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
