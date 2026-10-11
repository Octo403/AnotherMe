"""One authorized public-course PDF via the running preview's saved credentials."""
import json
import time
from pathlib import Path

import httpx


def main():
    ready=json.loads(Path('.tmp/question-bank-preview.json').read_text(encoding='utf-8'))
    started=time.monotonic()
    with httpx.Client(base_url=ready['url'],trust_env=False,timeout=30) as client:
        settings=client.get('/api/model-settings');settings.raise_for_status()
        if not settings.json()['mineru_has_token']:
            raise RuntimeError('MinerU Token not configured; no upload attempted')
        response=client.post('/api/mineru-sample',json={'source_id':729});response.raise_for_status()
        key=response.json()['job_id'];phase=None
        while time.monotonic()-started<600:
            response=client.get('/api/job',params={'id':key});response.raise_for_status();job=response.json()
            current=(job['status'],job['phase'])
            if current!=phase:
                print(json.dumps({'phase':current},ensure_ascii=False),flush=True);phase=current
            if job['status']!='running':break
            time.sleep(.5)
    report={'passed':job['status']=='complete','source_id':729,'course':'线性代数','seconds':time.monotonic()-started,
            'job':job,'note':'One real MinerU VLM API call on an authorized freshly fetched public PDF, isolated experiment output; no bank replacement, no personal data.'}
    Path('data/evaluation/question_bank/mineru-public-course-runtime.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    assert report['passed']


if __name__=='__main__':main()
