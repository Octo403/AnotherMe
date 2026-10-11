"""Real public-bank/context checks with isolated history; no remote model calls."""
import json
from pathlib import Path

import httpx

from aitown.config import Settings
from aitown.graph import AnotherMeAgent


def main():
    ready=json.loads(Path('.tmp/question-bank-preview.json').read_text(encoding='utf-8'))
    with httpx.Client(base_url=ready['url'],trust_env=False) as client:
        checks={name:client.get(path).status_code==200 for name,path in [
            ('health','/api/health'),('settings_api','/api/model-settings'),('model_js','/models.js'),('app_js','/app.js')]}
        settings=client.get('/api/model-settings').json()
        checks['credentials_not_returned']='api_key' not in settings['api'] and 'mineru_token' not in settings
    run=Path('.tmp/model-context-check')
    agent=AnotherMeAgent(Settings(question_bank_dir=Path('data/question_bank'),
        memory_db=run/'history.sqlite3',knowledge_dir=run/'knowledge'))
    try:
        first=agent.ask('找集合与图论的证明题','model-context-utf8')
        follow=agent.ask('我说的是证明题','model-context-utf8')
        changed=agent.ask('换成等价关系的证明题','model-context-utf8')
        results=[r.skills[0].data for r in (first,follow,changed)]
        checks['broad_proof_uses_filters']=results[0]['method']=='filters' and results[0]['count']>0
        checks['type_correction_keeps_course']=follow.tool_calls[0].name=='question_search' and results[1]['count']>0
        checks['topic_change_matches']=results[2]['count']>0 and all('等价关系' in q['topics'] for q in results[2]['results'])
        report={'checks':checks,'passed':all(checks.values()),'preview':ready['url'],
            'preview_model_mode':settings['search_mode'],'real_question_ids':[r['question_ids'] for r in results],
            'remote_calls':False,'visual_acceptance':'pending',
            'note':'真实公开题库和独立会话存储；不上传数据、不替换题库，不能据此推断语义匹配准确率。'}
        target=Path('data/evaluation/question_bank/model-settings.json')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        assert report['passed']
    finally:
        agent.close()


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
