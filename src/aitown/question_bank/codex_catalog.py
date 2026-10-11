"""Read the CLI's model directory without creating a thread or running inference."""
import json
import os
import queue
import subprocess
import tempfile
import threading
import time

from .chatgpt import codex_command


class _CatalogRPC:
    def __init__(self, process, timeout):
        self.process = process
        self.deadline = time.monotonic() + timeout
        self.messages = queue.Queue(maxsize=128)
        self.stopped = threading.Event()
        self.next_id = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while not self.stopped.is_set():
                line = self.process.stdout.readline(1024 * 1024 + 1)
                if not line or len(line) > 1024 * 1024:
                    break
                try:
                    value = json.loads(line)
                except ValueError:
                    break
                while not self.stopped.is_set():
                    try:
                        self.messages.put(value, timeout=.1)
                        break
                    except queue.Full:
                        continue
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.messages.put_nowait(None)
            except queue.Full:
                pass

    def send(self, method, params, request_id=None):
        value = {'method': method, 'params': params}
        if request_id is not None:
            value['id'] = request_id
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        self.send(method, params, request_id)
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                value = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError from None
            if not isinstance(value, dict):
                raise ValueError('Codex 型号目录连接中断，请检查 CLI 登录和版本。')
            if value.get('id') != request_id:
                continue  # Account notifications are deliberately not exposed.
            if 'error' in value or not isinstance(value.get('result'), dict):
                raise ValueError('Codex 型号目录读取失败，请检查 CLI 登录、网络或版本。')
            return value['result']

    def close(self):
        self.stopped.set()
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=3)  # EOF lets both the Node launcher and CLI exit.
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                # Only the process tree we started, including the npm launcher child.
                subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.reader.join(timeout=1)
        self.process.stdout.close()


def codex_model_catalog(timeout=25):
    """Use managed ChatGPT login, never inspect or return account credentials."""
    command = codex_command()
    env = {k: v for k, v in os.environ.items() if k not in ('OPENAI_API_KEY', 'CODEX_API_KEY')}
    try:
        with tempfile.TemporaryDirectory(prefix='anotherme-model-catalog-') as directory:
            process = subprocess.Popen(
                [*command, 'app-server', '--listen', 'stdio://',
                 '-c', 'forced_login_method="chatgpt"', '-c', 'model_provider="openai"'],
                cwd=directory, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            rpc = _CatalogRPC(process, timeout)
            try:
                rpc.request('initialize', {'clientInfo': {
                    'name': 'anotherme_models', 'title': 'AnotherMe Model Selector', 'version': '0.1.0'}})
                rpc.send('initialized', {})
                account = rpc.request('account/read', {'refreshToken': False}).get('account')
                if not isinstance(account, dict) or account.get('type') != 'chatgpt':
                    raise ValueError('Codex 尚未通过 ChatGPT 账号登录，请先运行 codex login，再刷新型号。')
                choices = []
                seen = set()
                cursor = None
                cursors = set()
                while True:
                    params = {'limit': 100, 'includeHidden': False}
                    if cursor:
                        params['cursor'] = cursor
                    page = rpc.request('model/list', params)
                    if not isinstance(page.get('data'), list):
                        raise ValueError('Codex 型号目录格式异常，请检查 CLI 版本。')
                    for item in page['data']:
                        if not isinstance(item, dict) or item.get('hidden'):
                            continue
                        model = item.get('model') or item.get('id')
                        if not isinstance(model, str) or not model.strip() or len(model) > 200 or model in seen:
                            continue
                        seen.add(model)
                        choices.append({'id': model, 'name': str(item.get('displayName') or model)[:200],
                                        'is_default': item.get('isDefault') is True,
                                        'supports_images': 'image' in item.get('inputModalities', ['text', 'image'])})
                    cursor = page.get('nextCursor')
                    if not cursor:
                        break
                    if not isinstance(cursor, str) or cursor in cursors or len(choices) > 1000:
                        raise ValueError('Codex 型号目录分页异常，请稍后刷新。')
                    cursors.add(cursor)
                if not choices:
                    raise ValueError('Codex 未返回可选择的型号，请检查登录、网络或 CLI 版本后刷新。')
                return {'models': [item['id'] for item in choices], 'choices': choices,
                        'message': '已读取 Codex 型号目录；实际使用权限与额度以调用结果为准。'}
            finally:
                rpc.close()
    except TimeoutError:
        raise ValueError('读取 Codex 型号超时，请检查网络后刷新。') from None
    except OSError:
        raise ValueError('无法启动或连接 Codex CLI，请检查安装和登录状态。') from None
