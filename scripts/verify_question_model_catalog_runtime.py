"""Read-only preview checks; catalog discovery does not run model inference."""
import json
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.tags[attrs['id']] = tag


def main():
    ready = json.loads(Path('.tmp/question-bank-preview.json').read_text(encoding='utf-8'))
    with httpx.Client(base_url=ready['url'], timeout=10) as client:
        assert client.get('/api/health').json()['status'] == 'ok'
        before = client.get('/api/model-settings').json()
        controls = Controls()
        controls.feed(client.get('/').text)
        assert controls.tags['model-id'] == controls.tags['codex-model-id'] == 'select'
        assert 'load-codex-models' in controls.tags
        script = client.get('/models.js')
        assert script.status_code == 200 and 'function modelOptions' in script.text
        started = time.monotonic()
        response = client.post('/api/model-catalog', json={'search_mode': 'codex_chatgpt'})
        response.raise_for_status()
        job_id = response.json()['job_id']
        while time.monotonic() - started < 40:
            job = client.get('/api/job', params={'id': job_id}).json()
            if job['status'] == 'failed':
                raise AssertionError(job['error'])
            if job['status'] == 'complete':
                break
            time.sleep(.2)
        else:
            raise AssertionError('Catalog job did not finish')
        catalog = job['result']
        assert catalog['models'] and catalog['models'] == [item['id'] for item in catalog['choices']]
        assert not any(key in json.dumps(catalog) for key in ('api_key', 'access_token', 'email'))
        assert client.get('/api/model-settings').json() == before
    report = {'time': datetime.now().astimezone().isoformat(), 'url': ready['url'],
              'checks': {'preview_healthy': True, 'both_model_controls_are_selects': True,
                         'updated_script_served': True, 'live_codex_catalog_returned': True,
                         'catalog_excludes_credentials_and_account_details': True,
                         'model_preferences_unchanged': True},
              'models': catalog['models'], 'choices': catalog['choices'],
              'seconds': round(time.monotonic() - started, 2), 'passed': True,
              'note': '真实预览 HTTP 与 Codex 目录；未执行推理，未验证型号额度或浏览器布局。'}
    Path('data/evaluation/question_bank/model-catalog.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
