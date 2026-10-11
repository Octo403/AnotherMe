"""GitHub tree discovery and bounded, atomic downloads. No attachment execution."""
import base64
import re
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from .models import COURSE_ID, COURSE_NAME, BankConfig, Source


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
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500 and exc.response.status_code != 429:
                    raise
                if attempt == 2:
                    raise
                time.sleep(.5 * (attempt + 1))

    def discover(self):
        repo = self.config.repository
        info = self.request('https://api.github.com/repos/'+repo).json()
        branch = info['default_branch']
        url = f'https://api.github.com/repos/{repo}/git/trees/{quote(branch, safe="")}'
        tree = self.request(url, params={'recursive': '1'}).json()
        entries = tree['tree']
        if tree.get('truncated'):
            # Nonrecursive tree endpoints are complete; traverse only resource directories.
            entries = []
            def walk(ref, prefix=''):
                data = self.request(f'https://api.github.com/repos/{repo}/git/trees/{ref}').json()
                if data.get('truncated'):
                    raise ValueError('非递归目录也被截断，不能声明资料清点完整')
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
