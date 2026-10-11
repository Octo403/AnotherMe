"""Verify a real Markdown paper through an isolated bank, HTTP and optional ChatGPT."""
import argparse
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

import httpx

from aitown.config import Settings
from aitown.question_bank.models import BankConfig, Source
from aitown.question_bank.prepare import parse_file
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.semantic import build_index
from aitown.ui.demo import settings_for_ui
from aitown.ui.server import make_server
from aitown.ui.service import UIService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--course', default='人工智能的软件基础')
    parser.add_argument('--chatgpt', action='store_true')
    args = parser.parse_args()
    run_id = uuid.uuid4().hex
    run = (Path('data/runtime/markdown-course')/run_id).resolve()
    report_path = Path('data/evaluation/question_bank')/f'markdown-course-{run_id}.json'
    installed = Settings.from_env(mode='demo')
    settings = settings_for_ui(installed.model_copy(update={'question_bank_dir': run/'bank',
        'question_similar_provider': 'codex_chatgpt' if args.chatgpt else 'none',
        'question_extractor': 'basic', 'question_pdf_ocr': 'none'}), run/'app')
    config = BankConfig(root=settings.question_bank_dir, embedding_dir=settings.local_embedding_dir,
                        reranker_dir=settings.local_reranker_dir)
    raw = config.root/'raw'/'paper.md'
    raw.parent.mkdir(parents=True);shutil.copyfile(args.source, raw)
    checks = {};samples = [];service = server = thread = client = None
    report = {'run': str(run), 'source': str(args.source), 'checks': checks, 'samples': samples,
              'note': '用户提供的 MD 独立副本；实际本地解析、BGE、HTTP、服务重启。ChatGPT 按参数启用；不写主库或个人资料。'}
    started = time.monotonic()

    def start():
        nonlocal service, server, thread, client
        service = UIService(settings, run/'ui.sqlite3');server = make_server(service, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        client = httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False, timeout=30)

    def stop():
        nonlocal service, server, thread, client
        if client:client.close();client = None
        if server:server.shutdown();server.server_close();thread.join();server = None
        if service:service.close();service = None

    def get(path, **kwargs):
        response = client.get(path, **kwargs);response.raise_for_status();return response.json()

    def post(path, payload):
        response = client.post(path, json=payload);response.raise_for_status();return response.json()

    def chat(query):
        job = post('/api/chat', {'session': session, 'question': '@'+args.course+' '+query})['job_id']
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            result = get('/api/job', params={'id': job})
            if result['status'] == 'complete':break
            if result['status'] == 'failed':raise RuntimeError(result['error'])
            time.sleep(.25)
        else:raise TimeoutError('Markdown 查询超时')
        reply = result['result']['reply']
        search = next(s['data'] for s in reply['skills'] if s['name'] == 'question_search')
        samples.append({'query': query, 'questions': reply['questions'], 'method': search['method'],
                        'model_calls': search.get('model_calls', []), 'warnings': reply['warnings']})
        print(json.dumps({'query': query, 'ids': [q['id'] for q in reply['questions']],
                          'method': search['method']}, ensure_ascii=False), flush=True)
        return reply, search

    try:
        with BankRepository(config.database) as repo:
            repo.save_discovery([{'id': args.course, 'name': args.course, 'path': 'local/reviewed/course'}], [])
            with repo.conn:
                sid = repo.save_source(Source(course_id=args.course, repository='local/reviewed',
                    path=args.source.name, url='https://github.com/victkk/FDU-Sharing/tree/master/public/resources'))
            repo.set_source(sid, local_path=str(raw))
            parsed = parse_file(raw, repo.source(sid), config.root/'processed'/str(sid), config)
            repo.replace_parsed(sid, parsed)
        assert len(parsed.questions) == 2
        checks['two_major_questions_without_generated_images'] = all(not q.images for q in parsed.questions)
        checks['resnet_keeps_both_subparts'] = all(s in parsed.questions[0].text for s in ('绘制残差网络', '计算给定残差结构'))
        checks['lecture_keeps_goals_schedule_architecture_and_rubric'] = all(s in parsed.questions[1].text for s in ('### a)', '### b)', '评分标准', '软件技术架构图'))
        report['index'] = build_index(config, course=args.course)
        start();session = post('/api/session', {})['id']
        html = client.get('/').text
        checks['separate_panels_and_scrollable_course_selector_served'] = all(s in html for s in ('id="materials"', 'id="settings"', 'role="combobox"')) and '<datalist' not in html
        asset = client.get('/question-text.js');asset.raise_for_status()
        checks['local_text_renderer_served'] = 'renderQuestionText' in asset.text
        first, first_search = chat('找残差网络相关的题')
        second, second_search = chat('找大模型系统软件科普讲座设计的题')
        checks['search_returns_complete_standard_text'] = all(r['questions'] and all(q['content_format'] == 'markdown' and q['text'] and not q['images'] for q in r['questions']) for r in (first, second))
        checks['different_queries_select_correct_major_question'] = [q['number'] for q in first['questions']] == ['1'] and [q['number'] for q in second['questions']] == ['2']
        if args.chatgpt:
            checks['real_chatgpt_selection_uses_text_without_screenshots'] = all(any(c['provider'] == 'codex_chatgpt' and c['status'] == 'ok' and c['image_count'] == 0 for c in s['model_calls']) for s in (first_search, second_search))
        qid = first['questions'][0]['id']
        response = client.get('/api/question-source', params={'id': qid, 'source': sid});response.raise_for_status()
        checks['original_markdown_retained_and_readable'] = response.content == args.source.read_bytes()
        checks['no_personal_data_or_source_answers'] = all(not r['profile'] and not r['style'] and all('answers' not in q for q in r['questions']) for r in (first, second))
        before = get('/api/history', params={'session': session});stop();start()
        after = get('/api/history', params={'session': session})
        checks['standard_text_and_source_lines_survive_restart'] = [[q['text'] for q in t['reply']['questions']] for t in before['turns']] == [[q['text'] for q in t['reply']['questions']] for t in after['turns']]
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        stop();report['seconds'] = time.monotonic()-started
        report['passed'] = bool(checks) and all(checks.values()) and 'error' not in report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'report': str(report_path), 'passed': report['passed'], 'checks': checks,
                          'error': report.get('error'), 'seconds': report['seconds']}, ensure_ascii=False, indent=2), flush=True)
    assert report['passed'], report.get('error', checks)


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8');main()
