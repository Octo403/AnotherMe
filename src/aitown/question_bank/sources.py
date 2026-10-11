"""GitHub tree discovery and bounded, atomic downloads. No attachment execution."""
import base64
import json
import re
import time
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from .models import COURSE_ID, COURSE_NAME, BankConfig, Source


def github_request_error(exc):
    """Explain failures without echoing proxy pages, URLs or credentials."""
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status = response.status_code
        limited = status == 429 or (status == 403 and response.headers.get('x-ratelimit-remaining') == '0')
        if status == 403 and not limited:
            try:
                limited = 'rate limit' in str(response.json().get('message', '')).lower()
            except (ValueError, AttributeError):
                pass
        if limited:
            wait = '请至少等一分钟后再试，避免连续刷新。'
            try:
                seconds = int(response.headers.get('retry-after', ''))
                if seconds > 0:
                    wait = f'请等待 {seconds} 秒后再试，避免连续刷新。'
            except ValueError:
                try:
                    reset = float(response.headers.get('x-ratelimit-reset', ''))
                    wait = '请在本机时间 '+datetime.fromtimestamp(reset).strftime('%Y-%m-%d %H:%M:%S')+' 后再试。'
                except (ValueError, OSError, OverflowError):
                    pass
            return f'GitHub 请求被限流（HTTP {status}）。{wait}同一网络出口的其他用户也可能共用额度。'
        if status == 404:
            detail = '公开资料仓库或分支未找到；请检查仓库地址，稍后重试。'
        elif status == 407:
            detail = '网络代理要求认证；请检查 Windows 或环境变量中的代理配置。'
        elif status in (401, 403):
            detail = '请求被拒绝；请检查当前网络、代理和仓库访问权限。'
        elif status >= 500:
            detail = '上游服务暂时异常；请稍后重试，并检查当前网络或代理。'
        else:
            detail = '请检查当前网络或代理后重试。'
        return f'GitHub 请求失败（HTTP {status}）。'+detail
    if isinstance(exc, httpx.TimeoutException):
        return '连接 GitHub 超时，请检查能否访问 api.github.com，以及当前网络或代理配置。'
    return '无法连接 GitHub，请检查能否访问 api.github.com，以及当前网络、代理或证书配置。'


def hints(path):
    name = PurePosixPath(path).name
    year = re.search(r'(?:19|20)\d{2}', name)
    category = 'answer' if re.search('答案|解答|解析', name) else 'practice' if re.search('练习|习题', name) else 'exam' if re.search('试卷|考试|期末|期中|midterm|final', name, re.I) else 'unknown'
    if '回忆' in path and category != 'answer':
        category = 'recollection'
    return {'category_hint': category, 'year_hint': int(year[0]) if year else None,
            'exam_hint': 'final' if re.search('期末|final', name, re.I) else 'midterm' if re.search('期中|midterm', name, re.I) else None}


class GithubSource:
    def __init__(self, config: BankConfig):
        self.config = config
        self.client = httpx.Client(timeout=config.timeout, follow_redirects=True, headers={'User-Agent': 'AnotherMe-question-bank'})

    def close(self):
        self.client.close()

    def request(self, url, **kwargs):
        for attempt in range(3):
            try:
                response = self.client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                # Rate limits can last minutes or an hour. Immediate retries
                # consume quota and must not hide the server's retry guidance.
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise ValueError(github_request_error(exc)) from None
                if attempt == 2:
                    raise ValueError(github_request_error(exc)) from None
                time.sleep(.5 * (attempt + 1))

    def json(self, url, **kwargs):
        response = self.request(url, **kwargs)
        try:
            data = response.json()
        except json.JSONDecodeError:
            raise ValueError('GitHub 返回的内容不是有效目录 JSON；请检查网络或代理是否拦截了 api.github.com。') from None
        if not isinstance(data, dict):
            raise ValueError('GitHub 目录格式异常，本次未更新本地资料。')
        return data

    def discover(self):
        repo = self.config.repository
        info = self.json('https://api.github.com/repos/'+repo)
        branch = info.get('default_branch')
        if not isinstance(branch, str) or not branch:
            raise ValueError('GitHub 未返回公开资料仓库的默认分支，本次未更新本地资料。')
        url = f'https://api.github.com/repos/{repo}/git/trees/{quote(branch, safe="")}'
        tree = self.json(url, params={'recursive': '1'})
        entries = tree.get('tree')
        if not isinstance(entries, list):
            raise ValueError('GitHub 未返回完整目录，本次未更新本地资料。')
        if tree.get('truncated'):
            # Nonrecursive tree endpoints are complete; traverse only resource directories.
            entries = []
            def walk(ref, prefix=''):
                data = self.json(f'https://api.github.com/repos/{repo}/git/trees/{ref}')
                if data.get('truncated'):
                    raise ValueError('非递归目录也被截断，不能声明资料清点完整')
                if not isinstance(data.get('tree'), list):
                    raise ValueError('GitHub 未返回完整子目录，本次未更新本地资料。')
                for item in data['tree']:
                    path = prefix + item['path']
                    entries.append({**item, 'path': path})
                    if item['type'] == 'tree' and (path in ('public', 'public/resources') or path.startswith('public/resources/')):
                        walk(item['sha'], path + '/')
            walk(quote(branch, safe=''))
        prefix = 'public/resources/'
        names = sorted({x['path'][len(prefix):].split('/')[0] for x in entries
            if x['path'].startswith(prefix) and '/' in x['path'][len(prefix):]})
        courses = [{'id': COURSE_ID if name == COURSE_NAME else name, 'name': name, 'path': prefix+name} for name in names]
        ids = {x['name']: x['id'] for x in courses}
        files = []
        for entry in entries:
            path = entry['path']
            if entry['type'] != 'blob' or not path.startswith(prefix) or '/' not in path[len(prefix):]:
                continue
            name = path[len(prefix):].split('/')[0]
            files.append(Source(course_id=ids[name], repository=repo, path=path,
                url=f'https://raw.githubusercontent.com/{repo}/{quote(branch, safe="")}/{quote(path, safe="/")}',
                size=entry.get('size'), **hints(path)))
        return courses, files

    def download(self, source, destination: Path, *, update=False):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and not update:
            if not destination.stat().st_size or (source['size'] is not None and destination.stat().st_size != source['size']):
                update = True
            else:
                return destination
        temporary = destination.with_suffix(destination.suffix+'.part')
        try:
            for attempt in range(3):
                try:
                    with self.client.stream('GET', source['url'], timeout=12) as response:
                        response.raise_for_status()
                        total = 0
                        with temporary.open('wb') as stream:
                            for chunk in response.iter_bytes():
                                total += len(chunk)
                                if total > self.config.max_download_bytes:
                                    raise ValueError('来源超过下载大小限制')
                                stream.write(chunk)
                    if not total or (source['size'] is not None and total != source['size']):
                        raise ValueError('来源下载不完整，实际字节数与目录不一致')
                    break
                except (httpx.TransportError, httpx.HTTPStatusError):
                    # Some networks cannot reach raw.githubusercontent.com. The
                    # GitHub contents/blob API serves the same Git object.
                    if source['repository'] == self.config.repository:
                        info = self.request(f"https://api.github.com/repos/{source['repository']}/contents/{quote(source['path'], safe='/')}").json()
                        if not info.get('content'):
                            info = self.request(info['git_url']).json()
                        content = base64.b64decode(info['content'], validate=False)
                        if not content or len(content) > self.config.max_download_bytes or (source['size'] is not None and len(content) != source['size']):
                            raise ValueError('API 返回的文件大小与目录不一致')
                        temporary.write_bytes(content)
                        break
                    if attempt == 2:
                        raise
                    time.sleep(.5*(attempt+1))
            temporary.replace(destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)
