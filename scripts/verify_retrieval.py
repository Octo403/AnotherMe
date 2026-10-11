"""Record current local CLI/graph checks without printing private evidence."""
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx

from aitown.config import Settings
from aitown.graph import AnotherMeAgent
from aitown.retrieval.corpus import items


def main():
    output = Path('data/private/t7/verification')
    output.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env(mode='demo')
    agent = AnotherMeAgent(settings)
    checks = {}
    try:
        refresh = agent.retriever.refresh()
        checks['refresh'] = refresh
        general = agent.ask('活动室最多能坐几个人？', session='t7-verify-general')
        unknown = agent.ask('我的月球旅游经历是什么', session='t7-verify-unknown', scope='c0067')
        ambiguous = agent.ask('我去过哪些地方', session='t7-verify-ambiguous', scope='')
        checks['general'] = {'status': general.retrieval_status, 'sources': len(general.sources), 'contains_20': '20' in general.answer}
        checks['unknown'] = {'status': unknown.retrieval_status, 'sources': len(unknown.sources), 'admits_unknown': '我不知道' in unknown.answer}
        checks['ambiguous'] = {'status': ambiguous.retrieval_status, 'requests_scope': '--scope' in ambiguous.answer}
        person = next(i for i in items(agent.retrieval_repository, 'personal') if i.subject == 'self' and i.status == 'confirmed' and i.category != 'plan')
        question = '我的偏好：' + person.text.split('\n', 1)[-1][:100]
        personal = agent.ask(question, session='t7-verify-personal', scope=person.scopes[0])
        checks['personal'] = {'status': personal.retrieval_status, 'sources': len(personal.sources),
                              'source_ids': [mid for s in personal.sources for mid in s.source_ids],
                              'all_self': all(s.subject == 'self' for s in personal.sources)}
        evidence = {'general': general.model_dump(), 'unknown': unknown.model_dump(),
                    'ambiguous': ambiguous.model_dump(), 'personal': personal.model_dump()}
        (output/'demo.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        agent.close()
    live_settings = Settings.from_env(mode='live')
    if urlparse(live_settings.base_url).hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('This verification only authorizes a local model endpoint')
    with httpx.Client(trust_env=False, timeout=3) as client:
        client.get(live_settings.base_url.rstrip('/').removesuffix('/v1')+'/health').raise_for_status()
    live = AnotherMeAgent(live_settings)
    try:
        reply = live.ask('活动室最多能坐几个人？', session='t7-verify-live')
        missing = live.ask('我的月球旅游经历是什么', session='t7-verify-live-unknown', scope='c0067')
        checks['live_general'] = {'status': reply.retrieval_status, 'contains_20': '20' in reply.answer,
                                  'sources': len(reply.sources), 'warnings': reply.warnings}
        checks['live_unknown'] = {'status': missing.retrieval_status, 'admits_unknown': '我不知道' in missing.answer,
                                  'sources': len(missing.sources)}
        (output/'live.json').write_text(json.dumps({'general': reply.model_dump(), 'unknown': missing.model_dump()},
                                                ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        live.close()
    assert checks['general']['contains_20'] and checks['live_general']['contains_20']
    assert checks['unknown']['status'] == checks['live_unknown']['status'] == 'no_personal_data'
    assert checks['ambiguous']['status'] == 'ambiguous_scope'
    assert checks['personal']['status'] == 'ok' and checks['personal']['all_self'] and checks['personal']['source_ids']
    (output/'summary.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
