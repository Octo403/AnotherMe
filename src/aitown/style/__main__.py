import argparse
import json
import sys
from pathlib import Path

from aitown.storage import Repository
from . import ProfileStore, StyleRetriever, statistics
from .evaluation import generate, prepare, summarize


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='Local style statistics, scoped retrieval and evaluation')
    parser.add_argument('--database',type=Path,default=Path('data/runtime/application.sqlite3'))
    sub=parser.add_subparsers(dest='command',required=True)
    stats=sub.add_parser('stats')
    stats.add_argument('--output',type=Path,required=True)
    profile=sub.add_parser('profile')
    profile.add_argument('--output',type=Path,required=True)
    apply=sub.add_parser('apply-profile')
    apply.add_argument('input',type=Path)
    search=sub.add_parser('search')
    search.add_argument('query')
    search.add_argument('--scope',required=True)
    search.add_argument('--scene',choices=['agree','refuse','comfort','explain','task','uncertain','chat'])
    evaluate=sub.add_parser('prepare-evaluation')
    evaluate.add_argument('--profile',type=Path,required=True)
    evaluate.add_argument('--scope',required=True)
    evaluate.add_argument('--output',type=Path,required=True)
    score=sub.add_parser('score')
    score.add_argument('directory',type=Path)
    run=sub.add_parser('generate-evaluation')
    run.add_argument('directory',type=Path)
    args=parser.parse_args()
    repo=Repository(args.database)
    try:
        if args.command=='stats':
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(statistics(repo),ensure_ascii=False,indent=2),encoding='utf-8')
            result={'output':str(args.output)}
        elif args.command=='profile':
            p=ProfileStore(repo)
            profile=p.apply(p.generate())
            p.write(profile,args.output)
            result={'claims':len(profile.claims),'output':str(args.output)}
        elif args.command=='apply-profile':
            p=ProfileStore(repo)
            profile=p.apply(p.read(args.input))
            p.write(profile,args.input)
            result={'claims':len(profile.claims)}
        elif args.command=='search':
            result=[h.model_dump() for h in StyleRetriever(repo).search(args.query,scope=args.scope,scene=args.scene)]
        elif args.command=='prepare-evaluation':
            result=prepare(repo,ProfileStore.read(args.profile),args.output,args.scope)
        elif args.command=='generate-evaluation':
            from aitown.config import Settings
            from aitown.llm import CompatibleModel
            model=CompatibleModel(Settings.from_env(mode='live'))
            try:result={'generated':generate(model,args.directory)}
            finally:model.close()
        else:result=summarize(args.directory)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:repo.close()


if __name__=='__main__':main()
