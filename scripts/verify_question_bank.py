"""Measure real local retrieval and optional account-authenticated similarity."""
import argparse
import json
import time
from pathlib import Path

from dotenv import load_dotenv, set_key

from aitown.config import Settings
from aitown.question_bank.models import BankConfig, SearchArgs
from aitown.question_bank.search import QuestionBank


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chatgpt', action='store_true')
    parser.add_argument('--configure', action='store_true')
    args = parser.parse_args()
    if args.configure:
        set_key('.env', 'QUESTION_SIMILAR_PROVIDER', 'codex_chatgpt')
        set_key('.env', 'MINERU_PYTHON', '.venv-mineru/Scripts/python.exe')
    load_dotenv(override=True)
    settings = Settings.from_env()
    config = BankConfig(root=settings.question_bank_dir, embedding_dir=settings.local_embedding_dir,
        reranker_dir=settings.local_reranker_dir, similar_provider=settings.question_similar_provider,
        similar_model=settings.question_similar_model)
    bank = QuestionBank(config)
    report = {'first_round': [], 'second_round': None}
    reference = None
    for query in ('等价关系', '偏序', '欧拉图'):
        started = time.perf_counter()
        result = bank.search(SearchArgs(query=query, limit=3))
        report['first_round'].append({'query': query, 'seconds': time.perf_counter()-started,
            'method': result.method, 'ids': result.question_ids, 'warnings': result.warnings,
            'model_calls': result.model_calls,
            'sources': [{'id':q['id'], 'number':q['number'], 'topics':q['topics'], 'url':q['url'],
                'pages':q['pages'], 'text':q['text']} for q in result.results]})
        print(f'{query}: {result.method}, {result.count} 题',flush=True)
        assert result.method == 'bge+keyword+bge_rerank', result.warnings
        assert not result.model_calls
        if query == '偏序' and result.results:
            reference = result.results[0]
    if args.chatgpt:
        assert reference, '需要一个真实参考题目'
        assert config.similar_provider == 'codex_chatgpt'
        result = bank.search(SearchArgs(query=' '.join(reference['topics']) or reference['text'],
            reference_ids=[reference['id']], exclude_ids=[reference['id']], limit=2))
        report['second_round'] = {'reference_id':reference['id'], **result.model_dump()}
    output = Path('data/evaluation/question_bank/two_rounds.json' if args.chatgpt else 'data/evaluation/question_bank/first_rounds.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report':str(output), 'first_round':[{
        k:v for k,v in r.items() if k!='sources'} for r in report['first_round']],
        'second_round':{k:v for k,v in report['second_round'].items() if k in
            ('status','question_ids','method','warnings','model_calls')} if report['second_round'] else None},
        ensure_ascii=False, indent=2))
    if args.chatgpt:
        assert 'chatgpt_similar' in result.method, result.warnings


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
