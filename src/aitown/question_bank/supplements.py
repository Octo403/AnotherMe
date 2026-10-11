"""User-supplied course sources: retained uploads and bounded public URL downloads."""
import ipaddress
import re
import socket
import tempfile
import time
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import httpx

from .models import COURSE_ID, COURSE_NAME, Source
from .sources import hints

UPLOAD_REPOSITORY = 'user:upload'
URL_REPOSITORY = 'user:url'
SUFFIXES = {'.pdf', '.doc', '.docx', '.md', '.txt', '.zip', '.rar', '.7z', '.html', '.htm'}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_HTML_BYTES = 4 * 1024 * 1024


def filename(value):
    if (not isinstance(value, str) or not value or len(value) > 180
            or value != PurePosixPath(value.replace('\\', '/')).name
            or re.search(r'[\x00-\x1f<>:"/\\|?*]', value) or value.endswith((' ', '.'))
            or re.match(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value, re.I)):
        raise ValueError('文件名无效，请使用不包含路径的普通文件名')
    if Path(value).suffix.lower() not in SUFFIXES:
        raise ValueError('支持 PDF、Word、MD、TXT、HTML 和 ZIP/RAR/7z 文件')
    return value


def normalize_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or re.search(r'[\x00-\x20\x7f\\]', value.strip()):
        raise ValueError('请输入有效的 HTTP / HTTPS 网址')
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError('网址须使用 HTTP / HTTPS，且不能包含账号密码')
    host = parts.hostname.encode('idna').decode('ascii').lower().rstrip('.')
    if not host or '%' in host:
        raise ValueError('网址主机名无效')
    port = parts.port
    authority = '['+host+']' if ':' in host else host
    if port and port != (443 if parts.scheme.lower() == 'https' else 80):authority += ':'+str(port)
    return urlunsplit((parts.scheme.lower(), authority, parts.path or '/', parts.query, ''))


def public_addresses(url):
    parts = urlsplit(url)
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(
        parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80), type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('只能获取公网网址，不能访问本机、内网或保留地址')
    return addresses


def ensure_course(repo, value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100 or re.search(r'[\x00-\x1f]', value):
        raise ValueError('请填写有效课程名（最多 100 字）')
    value = value.strip()
    row = repo.conn.execute('SELECT id,name FROM courses WHERE id=? OR name=?', (value, value)).fetchone()
    if row:return dict(row)
    cid = COURSE_ID if value == COURSE_NAME else value
    repo.conn.execute('INSERT INTO courses(id,name,path) VALUES(?,?,?)', (cid, value, 'local/courses/'+quote(cid, safe='')))
    return {'id': cid, 'name': value}


def check_replacement(repo, course, replace):
    if replace and repo.conn.execute("SELECT 1 FROM jobs WHERE course_id=? AND status IN ('queued','running')", (course,)).fetchone():
        raise ValueError('该课程任务正在运行，请处理结束后再替换资料；现在仍可补充新的文件或网址')


def invalidate_members(repo, source):
    repo.conn.execute("UPDATE sources SET remote_removed=1 WHERE course_id=? AND repository=? AND path=? AND member<>''",
                      (source['course_id'],source['repository'],source['path']))


def raw_target(directory, name, *, previous=False):
    directory.mkdir(parents=True,exist_ok=True)
    # Save replacements beside earlier bytes, then switch the SQLite pointer.
    # Windows ZIP readers can otherwise prevent overwriting an original.
    if previous or (directory/name).exists():
        directory=Path(tempfile.mkdtemp(prefix='revision-',dir=directory))
    return directory/name


def add_urls(repo, course, urls, *, replace=False):
    if not isinstance(urls, list) or not 1 <= len(urls) <= 20:
        raise ValueError('每次可补充 1–20 个网址')
    normalized = list(dict.fromkeys(normalize_url(url) for url in urls))
    results = []
    with repo.conn:
        repo.conn.execute('BEGIN IMMEDIATE')
        selected = ensure_course(repo, course)
        check_replacement(repo, selected['id'], replace)
        for url in normalized:
            existing = repo.conn.execute("SELECT * FROM sources WHERE course_id=? AND repository=? AND url=? AND member=''",
                                         (selected['id'], URL_REPOSITORY, url)).fetchone()
            if existing:
                if replace:
                    invalidate_members(repo,existing)
                    repo.conn.execute("UPDATE sources SET status='discovered',error=NULL WHERE id=?", (existing['id'],))
                results.append({'id': existing['id'], 'name': PurePosixPath(existing['path']).name, 'reused': not replace})
                continue
            name = unquote(PurePosixPath(urlsplit(url).path).name) or 'webpage.html'
            try:name = filename(name)
            except ValueError:name = 'webpage.html'
            sid = repo.save_source(Source(course_id=selected['id'], repository=URL_REPOSITORY,
                path='supplement/urls/'+quote(url, safe='')+'/'+name, url=url, **hints(name)))
            repo.conn.execute('UPDATE sources SET path=? WHERE id=?', ('supplement/urls/'+str(sid)+'/'+name,sid))
            results.append({'id': sid, 'name': name, 'reused': False})
    return selected, results


def add_uploads(repo, config, course, files, *, replace=False):
    if not isinstance(files, list) or not 1 <= len(files) <= 20:
        raise ValueError('每次可上传 1–20 个文件')
    names = [filename(item['name']) for item in files]
    if len({name.casefold() for name in names}) != len(names):raise ValueError('本批文件包含重复文件名，请先改名')
    if any(not isinstance(item['content'], bytes) or not item['content'] for item in files):raise ValueError('不能上传空文件')
    if sum(len(item['content']) for item in files) > min(MAX_UPLOAD_BYTES, config.max_download_bytes):raise ValueError('本批上传文件合计不能超过 100 MiB')
    results = []
    with repo.conn:
        repo.conn.execute('BEGIN IMMEDIATE')
        selected = ensure_course(repo, course)
        check_replacement(repo, selected['id'], replace)
    for name, item in zip(names, files):
        with repo.conn:
            repo.conn.execute('BEGIN IMMEDIATE')
            check_replacement(repo, selected['id'], replace)
            path = 'supplement/uploads/'+name
            existing = repo.conn.execute("SELECT * FROM sources WHERE course_id=? AND repository=? AND path=? COLLATE NOCASE AND member=''",
                                         (selected['id'], UPLOAD_REPOSITORY, path)).fetchone()
            if existing and not replace:
                results.append({'id': existing['id'], 'name': name, 'reused': True});continue
            if existing:
                invalidate_members(repo,existing)
                path = existing['path'];name = PurePosixPath(path).name
            sid = repo.save_source(Source(course_id=selected['id'], repository=UPLOAD_REPOSITORY,
                path=path, url='', size=len(item['content']), **hints(name)))
            directory = (config.root/'raw'/str(sid)).absolute()
            if not directory.resolve().is_relative_to((config.root/'raw').resolve()):raise ValueError('上传目标超出资料目录')
            try:
                target=raw_target(directory,name,previous=bool(existing and existing['local_path']))
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
                    temporary_path = Path(temporary.name)
                    try:temporary.write(item['content']);temporary.close();temporary_path.replace(target)
                    finally:temporary_path.unlink(missing_ok=True)
            except OSError as exc:
                repo.conn.execute("UPDATE sources SET status='failed',error=? WHERE id=?", (f'保存失败：{exc}'[:500],sid))
                results.append({'id':sid,'name':name,'error':'本地保存失败，请重新上传','reused':False});continue
            repo.conn.execute("UPDATE sources SET size=?,local_path=?,status='downloaded',error=NULL,raw_deleted_at=NULL WHERE id=?",
                              (len(item['content']), str(target), sid))
            results.append({'id': sid, 'name': name, 'reused': False})
    return selected, results


def _remote_filename(url, response, prefix):
    header = Message();header['Content-Disposition'] = response.headers.get('content-disposition', '')
    proposed = header.get_filename() or unquote(PurePosixPath(urlsplit(url).path).name)
    kind = response.headers.get('content-type', '').split(';')[0].lower()
    suffix = '.pdf' if prefix.lstrip().startswith(b'%PDF-') else {
        'application/pdf': '.pdf', 'text/html': '.html', 'application/xhtml+xml': '.html',
        'text/markdown': '.md', 'text/plain': '.txt', 'application/msword': '.doc',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
        'application/zip': '.zip', 'application/x-7z-compressed': '.7z', 'application/vnd.rar': '.rar',
    }.get(kind)
    if kind == 'text/plain' and Path(proposed or '').suffix.lower() in ('.md','.txt'):
        suffix = Path(proposed).suffix.lower()
    if not suffix and proposed:
        candidate = Path(proposed).suffix.lower()
        if candidate in SUFFIXES:suffix = candidate
    if not suffix and prefix.lstrip().lower().startswith((b'<!doctype html', b'<html')):suffix = '.html'
    if not suffix:raise ValueError('网址返回的文件格式暂不支持，请使用资料文件直链或普通网页')
    proposed = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', proposed or 'webpage')[:140].rstrip(' .') or 'webpage'
    if Path(proposed).suffix.lower() != suffix:proposed = Path(proposed).stem+suffix
    try:return filename(proposed)
    except ValueError:return 'resource'+suffix


def download_url(repo, source, config, *, update=False):
    current = Path(source['local_path']) if source['local_path'] else None
    if current and not current.resolve().is_relative_to((config.root/'raw').resolve()):raise ValueError('资料路径超出本地库')
    if current and current.is_file() and not update and source['status'] not in ('discovered','failed') and current.stat().st_size:return current
    url = normalize_url(source['url'])
    directory = config.root/'raw'/str(source['id'])
    if not directory.resolve().is_relative_to((config.root/'raw').resolve()):raise ValueError('资料路径超出本地库')
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic()+config.timeout
    with httpx.Client(timeout=config.timeout, follow_redirects=False, trust_env=False,
                      limits=httpx.Limits(max_keepalive_connections=0)) as client:
        for redirect in range(6):
            parts = urlsplit(url)
            addresses = public_addresses(url)
            last_error = None
            for address in addresses:
                authority = '['+address+']' if ':' in address else address
                if parts.port:authority += ':'+str(parts.port)
                pinned = urlunsplit((parts.scheme, authority, parts.path, parts.query, ''))
                temporary_path = None
                try:
                    remaining=max(.1,deadline-time.monotonic())
                    with client.stream('GET', pinned, headers={'Host': parts.netloc, 'User-Agent': 'AnotherMe-course-supplement'},
                                       extensions={'sni_hostname': parts.hostname},
                                       timeout=httpx.Timeout(remaining,connect=min(5,remaining))) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            url = normalize_url(urljoin(url, response.headers['location']));break
                        response.raise_for_status()
                        maximum = min(config.max_download_bytes, MAX_HTML_BYTES) if 'html' in response.headers.get('content-type', '') else config.max_download_bytes
                        length = response.headers.get('content-length')
                        if length and int(length) > maximum:raise ValueError('网址资料超过下载大小限制')
                        prefix = b'';total = 0
                        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
                            temporary_path = Path(temporary.name)
                            for chunk in response.iter_bytes():
                                total += len(chunk)
                                if total > maximum or time.monotonic() > deadline:raise ValueError('网址资料过大或下载超时')
                                if len(prefix) < 1024:prefix += chunk[:1024-len(prefix)]
                                temporary.write(chunk)
                        if not total:raise ValueError('网址返回空内容')
                        name = _remote_filename(url, response, prefix)
                        target = raw_target(directory,name,previous=bool(source['local_path']));temporary_path.replace(target)
                        with repo.conn:
                            invalidate_members(repo,source)
                            hint=hints(name)
                            repo.conn.execute("UPDATE sources SET local_path=?,size=?,path=?,resolved_url=?,category_hint=?,year_hint=?,exam_hint=?,raw_deleted_at=NULL WHERE id=?",
                                (str(target), total, 'supplement/urls/'+str(source['id'])+'/'+name, url,
                                 hint['category_hint'],hint['year_hint'],hint['exam_hint'],source['id']))
                        return target
                except httpx.TransportError as exc:
                    last_error = exc
                finally:
                    if temporary_path:temporary_path.unlink(missing_ok=True)
            else:
                raise ValueError('网址连接失败，请检查公开链接是否可访问（'+type(last_error).__name__+'）') from last_error
        raise ValueError('网址重定向次数过多')


class WebText(HTMLParser):
    def __init__(self, base=''):
        super().__init__(convert_charrefs=True);self.base=base;self.parts=[];self.links=[];self.ignored=[];self.anchor=None;self.title=False;self.titles=[];self.lists=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag in ('script','style','noscript','svg','nav','footer','header'):
            self.ignored.append(tag);return
        if self.ignored:return
        if tag=='base' and attrs.get('href'):self.base=urljoin(self.base,attrs['href'])
        if tag in ('ol','ul'):self.lists.append(int(attrs.get('start','1'))-1 if attrs.get('start','1').isdigit() and tag=='ol' else None)
        if tag=='title':self.title=True
        if tag in ('p','div','section','article','br','li','tr','blockquote'):self.parts.append('\n')
        if tag in ('td','th'):self.parts.append(' | ')
        if tag=='img':self.parts.append(' [图片：'+(attrs.get('alt') or '未提供说明')+'；请查看原网页] ')
        if tag in ('h1','h2','h3','h4','h5','h6'):self.parts.append('\n'+'#'*int(tag[1])+' ')
        if tag=='li':
            if self.lists and self.lists[-1] is not None:self.lists[-1]+=1;self.parts.append(str(self.lists[-1])+'. ')
            else:self.parts.append('- ')
        if tag=='a' and attrs.get('href'):self.anchor={'url':urljoin(self.base,attrs['href']),'label':''}
    def handle_endtag(self, tag):
        if self.ignored:
            if tag==self.ignored[-1]:self.ignored.pop()
            return
        if tag=='title':self.title=False
        if tag in ('ol','ul') and self.lists:self.lists.pop()
        if tag=='a' and self.anchor:
            link=self.anchor;self.anchor=None
            if urlsplit(link['url']).scheme in ('http','https') and Path(urlsplit(link['url']).path).suffix.lower() in SUFFIXES and len(self.links)<50:
                if not any(item['url']==link['url'] for item in self.links):self.links.append(link)
        if tag in ('p','div','section','article','li','tr','blockquote','h1','h2','h3','h4','h5','h6'):self.parts.append('\n')
    def handle_data(self, data):
        if self.ignored:return
        if self.title:self.titles.append(data);return
        self.parts.append(data)
        if self.anchor:self.anchor['label']+=data
    def result(self):
        text=re.sub(r'\n[ \t]*\n+', '\n\n', ''.join(self.parts)).strip()
        return text, self.links


def read_web(path, url=''):
    raw=Path(path).read_bytes()
    if len(raw)>MAX_HTML_BYTES:raise ValueError('网页正文不能超过 4 MiB')
    declared=re.search(br'charset\s*=\s*["\x27]?([a-zA-Z0-9_-]+)',raw[:4096])
    encoding=declared[1].decode('ascii') if declared else 'utf-8-sig'
    try:text=raw.decode(encoding)
    except (UnicodeDecodeError,LookupError):text=raw.decode('gb18030', errors='replace')
    parser=WebText(url);parser.feed(text);return parser.result()
