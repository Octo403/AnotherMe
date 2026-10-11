import time

import httpx
import pytest

from aitown.config import Settings
from aitown.question_bank.models import BankConfig, Source
from aitown.question_bank.repository import BankRepository
from aitown.question_bank.sources import GithubSource
from aitown.ui.demo import settings_for_ui
from aitown.ui.service import UIService


def install_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.parametrize('status,headers,body,expected', [
    (403, {'x-ratelimit-remaining':'0','x-ratelimit-reset':'2000000000'}, {}, '本机时间'),
    (403, {}, {'message':'You have exceeded a secondary rate limit'}, '至少等一分钟'),
    (429, {'retry-after':'120'}, {}, '120 秒'),
    (403, {}, {'message':'private-proxy-secret'}, '请求被拒绝'),
    (404, {}, {}, '仓库或分支未找到'),
    (407, {}, {}, '代理要求认证'),
])
def test_http_failure_has_status_recovery_and_no_rapid_retry(monkeypatch, status, headers, body, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers=headers, json=body)
    install_transport(monkeypatch, handler)
    remote = GithubSource(BankConfig())
    try:
        with pytest.raises(ValueError, match=expected) as error:
            remote.discover()
        assert f'HTTP {status}' in str(error.value)
        assert 'private-proxy-secret' not in str(error.value)
        assert len(calls) == 1
    finally:
        remote.close()


def test_transient_server_error_retries_and_uses_real_default_branch(monkeypatch):
    calls = []
    monkeypatch.setattr('aitown.question_bank.sources.time.sleep', lambda _: None)
    def handler(request):
        calls.append(request)
        if len(calls) == 1: return httpx.Response(503)
        if request.url.path.endswith('/FDU-Sharing'):
            return httpx.Response(200,json={'default_branch':'master'})
        assert request.url.path.endswith('/trees/master')
        return httpx.Response(200,json={'tree':[{'path':'public/resources/Test/exam.pdf','type':'blob','size':15}], 'truncated':False})
    install_transport(monkeypatch,handler)
    remote = GithubSource(BankConfig())
    try:
        courses, sources = remote.discover()
        assert [c['name'] for c in courses] == ['Test']
        assert sources[0].url == 'https://raw.githubusercontent.com/victkk/FDU-Sharing/master/public/resources/Test/exam.pdf'
        assert len(calls) == 3
    finally:
        remote.close()


@pytest.mark.parametrize('response', [httpx.Response(200,text='<html>proxy-secret</html>'),
                                     httpx.Response(200,json={'default_branch':'master'})])
def test_proxy_or_incomplete_json_does_not_replace_saved_catalog(tmp_path,monkeypatch,response):
    settings = settings_for_ui(Settings(question_bank_dir=tmp_path/'bank'),tmp_path/'app')
    with BankRepository(settings.question_bank_dir/'question_bank.sqlite3') as repo:
        repo.save_discovery([{'id':'Test','name':'Test','path':'public/resources/Test'}], [
            Source(course_id='Test',repository='victkk/FDU-Sharing',path='public/resources/Test/exam.pdf',url='https://example.org/exam.pdf')])
    install_transport(monkeypatch,lambda request: response)
    service = UIService(settings,tmp_path/'ui.sqlite3')
    try:
        key = service.start_job('_discover_courses',{})['job_id']
        deadline = time.monotonic()+5
        while (job := service.job(key))['status'] == 'running' and time.monotonic()<deadline:
            time.sleep(.01)
        assert job['status'] == 'failed'
        assert '原有课程目录和已收集资料均已保留' in job['error']
        assert 'proxy-secret' not in job['error']
        assert any(c['id'] == 'Test' and c['files'] == 1 for c in service.call('_bootstrap')['question_courses'])
    finally:
        service.close()
