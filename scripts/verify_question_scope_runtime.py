"""Exercise @ scope against the public bank, with independent fictional UI history."""
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from aitown.config import Settings
from aitown.question_bank.models import COURSE_ID
from aitown.ui.demo import settings_for_ui
from aitown.ui.server import make_server
from aitown.ui.service import UIService


def bank_contents():
    database = Path('data/question_bank/question_bank.sqlite3').resolve()
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as connection:
        return {table: connection.execute('SELECT * FROM '+table+' ORDER BY 1,2').fetchall()
                for table in ('courses', 'sources', 'questions', 'pages', 'answers', 'sqlite_sequence')}


def main():
    before = bank_contents()
    directory = Path('.tmp/question-scope-runtime-check')
    config = settings_for_ui(Settings(question_bank_dir=Path('data/question_bank')), directory)
    service = UIService(config, directory/'ui.sqlite3')
    server = make_server(service, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    checks = {}; samples = []
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as client:
            one = client.post('/api/session', json={'scope': 'synthetic'}).json()['id']
            two = client.post('/api/session', json={'scope': 'synthetic'}).json()['id']
            checks['scope_script_served'] = client.get('/question-scope.js').status_code == 200
            def ask(session, question):
                key = client.post('/api/chat', json={'session': session, 'question': question}).json()['job_id']
                deadline = time.monotonic()+20
                while time.monotonic()<deadline:
                    job = client.get('/api/job', params={'id': key}).json()
                    if job['status'] != 'running':break
                    time.sleep(.03)
                assert job['status'] == 'complete', job
                reply = job['result']['reply']
                samples.append({'question': question, 'tool_names': [call['name'] for call in reply['debug']['decision']['calls']],
                                'question_ids': [q['id'] for q in reply['questions']]})
                return reply
            first = ask(one, '@集合与图论 排列组合的有哪些')
            checks['explicit_mention_routes_to_public_bank'] = bool(first['questions']) and first['skills'][0]['name'] == 'question_search'
            checks['fixed_scope_persisted'] = client.get('/api/history', params={'session': one}).json()['session']['question_course'] == COURSE_ID
            follow = ask(one, '排列组合的有哪些')
            checks['plain_followup_stays_in_course'] = bool(follow['questions']) and follow['skills'][0]['name'] == 'question_search'
            changed = ask(one, '等价关系的有哪些')
            checks['new_topic_stays_in_course'] = bool(changed['questions']) and all('等价关系' in q['topics'] for q in changed['questions'])
            checks['course_reply_excludes_personal_style_and_ocr'] = all(not r['profile'] and not r['style'] and all('text' not in q for q in r['questions']) for r in (first, follow, changed))
            checks['other_session_unpinned'] = not ask(two, '排列组合的有哪些')['questions']
            client.post('/api/question-scope', json={'session': one, 'course': ''}).raise_for_status()
            checks['cancel_restores_automatic_routing'] = not ask(one, '排列组合的有哪些')['questions']
        ready = json.loads(Path('.tmp/question-bank-preview.json').read_text(encoding='utf-8'))
        with httpx.Client(base_url=ready['url'], trust_env=False) as client:
            checks['preview_serves_scope_widget_and_script'] = ('question-course-options' in client.get('/').text and client.get('/question-scope.js').status_code == 200)
    finally:
        server.shutdown(); server.server_close(); worker.join(); service.close()
    after = bank_contents()
    checks['real_bank_tables_unchanged'] = before == after
    report = {'time': datetime.now().astimezone().isoformat(), 'checks': checks, 'passed': all(checks.values()),
              'real_question_count': len(after['questions']), 'samples': samples,
              'note': '真实公开题库与HTTP/Agent，独立合成个人资料和会话；原始记录数包含重复和待审校项，未调用在线模型，不代表相关性或浏览器视觉验收。'}
    Path('data/evaluation/question_bank/question-scope.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert report['passed']


if __name__ == '__main__':
    main()
