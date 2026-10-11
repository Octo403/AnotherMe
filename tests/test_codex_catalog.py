import json
import subprocess
import sys

import pytest

from aitown.config import Settings
from aitown.question_bank import codex_catalog
from aitown.ui.demo import settings_for_ui
from aitown.ui.service import UIService


@pytest.fixture
def catalog_cli(tmp_path, monkeypatch):
    """A real child process exercises framing, handshake, pagination and shutdown."""
    script = tmp_path / 'catalog_cli.py'
    script.write_text('''
import json, os, sys, time
mode = os.environ.get('CATALOG_TEST_MODE', 'ok')
initialized = False
assert 'OPENAI_API_KEY' not in os.environ and 'CODEX_API_KEY' not in os.environ
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    if method == 'initialized':
        initialized = True
        continue
    assert method in ('initialize', 'account/read', 'model/list')
    if method == 'initialize':
        result = {'userAgent': 'fixture'}
    elif method == 'account/read':
        assert initialized and request['params'] == {'refreshToken': False}
        result = {'account': {'type': 'apiKey' if mode == 'wrong_auth' else 'chatgpt',
                              'email': 'private-fixture@example.org'}}
    else:
        assert initialized and request['params']['includeHidden'] is False
        if mode == 'timeout':
            time.sleep(10)
        if mode == 'rpc_error':
            print(json.dumps({'id': request['id'], 'error': {'message': 'private-secret'}}), flush=True)
            continue
        if mode == 'empty':
            result = {'data': []}
        elif mode == 'bad_format':
            result = {'data': {}}
        elif request['params'].get('cursor'):
            assert request['params']['cursor'] == 'second-page'
            result = {'data': [{'id': 'first', 'model': 'exact-first'}, {'id': 'second'}]}
        else:
            result = {'data': [{'id': 'first', 'model': 'exact-first', 'displayName': 'First',
                                'isDefault': True, 'inputModalities': ['text']},
                               {'id': 'hidden', 'hidden': True}, {}, None], 'nextCursor': 'second-page'}
    print(json.dumps({'method': 'account/updated', 'params': {'email': 'private-fixture@example.org'}}), flush=True)
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
''', encoding='utf-8')
    monkeypatch.setattr(codex_catalog, 'codex_command', lambda: [sys.executable, str(script)])
    monkeypatch.setenv('OPENAI_API_KEY', 'private-secret')
    monkeypatch.setenv('CODEX_API_KEY', 'private-secret')
    processes = []
    original = subprocess.Popen

    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, 'Popen', launch)
    return processes


def test_catalog_reads_exact_ids_and_default_without_account_data(catalog_cli):
    result = codex_catalog.codex_model_catalog()
    assert result['models'] == ['exact-first', 'second']
    assert result['choices'][0] == {'id': 'exact-first', 'name': 'First', 'is_default': True, 'supports_images': False}
    assert result['choices'][1]['supports_images'] is True  # Older CLI compatibility.
    assert 'private' not in json.dumps(result)
    assert catalog_cli[0].poll() is not None and catalog_cli[0].stdout.closed


@pytest.mark.parametrize('mode,expected', [
    ('wrong_auth', 'ChatGPT'), ('rpc_error', '读取失败'), ('empty', '未返回'),
    ('bad_format', '格式异常'), ('timeout', '超时')])
def test_catalog_failures_are_sanitized_and_child_is_closed(catalog_cli, monkeypatch, mode, expected):
    monkeypatch.setenv('CATALOG_TEST_MODE', mode)
    with pytest.raises(ValueError, match=expected) as error:
        codex_catalog.codex_model_catalog(timeout=.5 if mode == 'timeout' else 5)
    assert 'private' not in str(error.value)
    assert catalog_cli[0].poll() is not None and catalog_cli[0].stdout.closed


def test_ui_catalog_routes_chatgpt_without_needing_api_key(tmp_path, monkeypatch):
    expected = {'models': ['account-model'], 'choices': [{'id': 'account-model'}]}
    monkeypatch.setattr(codex_catalog, 'codex_model_catalog', lambda: expected)
    config = settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'), tmp_path/'data')
    service = UIService(config, tmp_path/'ui.sqlite3')
    try:
        assert service.call('_model_catalog', {'search_mode': 'codex_chatgpt'}) == expected
        public = service.call('_save_model_settings', {'search_mode': 'codex_chatgpt', 'codex_model': 'account-model'})
        assert public['codex_model'] == 'account-model'
        assert service.pool.submit(lambda: service.agent.question_bank.config.similar_model).result() == 'account-model'
    finally:
        service.close()
