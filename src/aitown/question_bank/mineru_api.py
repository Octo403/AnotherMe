"""MinerU cloud v4, limited to the public course originals authorized by the user."""
import io
import ipaddress
import json
import re
import tempfile
import time
import zipfile
from pathlib import Path,PurePosixPath
from urllib.parse import unquote, urlparse

import httpx

from .mineru import parsed_content

API = 'https://mineru.net/api/v4/'


def public_url(value):
    url = urlparse(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.hostname == 'localhost':
        raise ValueError('MinerU 返回了不安全的上传或下载地址')
    try:
        address = ipaddress.ip_address(url.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError('MinerU 返回了非公网地址')
    return value


def validate_source(source):
    from .archives import ARCHIVE_SUFFIXES
    path = source['path']
    url = urlparse(source['url'])
    remote = unquote(url.path).split('/', 4)
    member=source.get('member') or ''
    parts=member.split('!/') if member else []
    safe_members=not parts or (len(parts)<=4 and PurePosixPath(path).suffix.lower() in ARCHIVE_SUFFIXES
        and all(PurePosixPath(p.replace('\\','/')).parts and not PurePosixPath(p.replace('\\','/')).is_absolute()
            and '..' not in PurePosixPath(p.replace('\\','/')).parts and ':' not in p for p in parts)
        and all(PurePosixPath(p).suffix.lower() in ARCHIVE_SUFFIXES for p in parts[:-1])
        and parts[-1].lower().endswith('.pdf'))
    if source['repository'] != 'victkk/FDU-Sharing' or not (
        path.startswith('public/resources/') and len(PurePosixPath(path).parts)>=4
        and (bool(member) or path.lower().endswith('.pdf')) and safe_members
        and '..' not in PurePosixPath(path).parts and '\\' not in path and url.scheme == 'https' and not url.query and not url.fragment
        and url.hostname == 'raw.githubusercontent.com' and not url.username
        and url.port in (None,443)
        and len(remote) == 5 and remote[1:3] == ['victkk', 'FDU-Sharing'] and remote[4] == path
    ):
        raise ValueError('MinerU 上传仅获准用于 FDU-Sharing 公开课程 PDF 及公开压缩包内 PDF；此文件不在授权范围内')


def normalize_bundle(raw, config):
    # Read bounded JSON members in memory; never extract archive paths.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if len(members) > config.max_archive_members or sum(m.file_size for m in members) > config.max_archive_bytes:
            raise ValueError('MinerU 结果压缩包超过大小或文件数量限制')
        names = [m.filename for m in members]
        preferred = next((n for n in names if n.endswith('structured_content.json')), None)
        if preferred:
            return json.loads(archive.read(preferred))
        layout = next((n for n in names if n.endswith(('layout.json', 'middle_json.json', '_middle.json'))), None)
        if layout:
            data = json.loads(archive.read(layout))
            if 'pdf_info' in data:
                pages = []
                for page in data['pdf_info']:
                    width, height = page['page_size']
                    blocks = []
                    for block in page.get('preproc_blocks', []):
                        text = '\n'.join(''.join(str(s.get('content', '')) for s in line.get('spans', []))
                            for line in block.get('lines', []))
                        bbox = block.get('bbox')
                        blocks.append({'type': block.get('type', 'text'), 'content': text,
                            'bbox': [bbox[0]/width, bbox[1]/height, bbox[2]/width, bbox[3]/height] if bbox else None})
                    pages.append({'page_idx': page['page_idx'], 'blocks': blocks})
                return {'pages': pages}
        content_name = next((n for n in names if n.endswith('content_list.json')), None)
        if not content_name:
            raise ValueError('MinerU 结果缺少可关联页码与区域的 JSON，未导入题库')
        data = json.loads(archive.read(content_name))
        pages = {}
        for item in data:
            index = int(item['page_idx'])
            bbox = item.get('bbox')
            pages.setdefault(index, []).append({'type': item.get('type', 'text'),
                'content': item.get('text') or item.get('table_body') or '[公式或图表见原图]',
                'bbox': [v/1000 for v in bbox] if bbox else None})
        return {'pages': [{'page_idx': index, 'blocks': blocks} for index, blocks in sorted(pages.items())]}


def parse_mineru_api_pdf(path, source, output, config):
    from .sources import GithubSource
    validate_source(source)
    if not config.mineru_api_key:
        raise ValueError('请在模型设置中填写 MinerU Token')
    # Fetch a pristine public copy so local annotations/private substitutions
    # cannot accidentally leave the machine under a public source label.
    with tempfile.TemporaryDirectory(prefix='anotherme-public-course-') as directory:
        directory=Path(directory)
        remote = GithubSource(config)
        try:
            public_source={**source,'member':'','size':None} if source.get('member') else source
            original = remote.download(public_source,directory/PurePosixPath(source['path']).name,update=True)
        finally:
            remote.close()
        if source.get('member'):
            from .archives import extract_member
            for level,name in enumerate(source['member'].split('!/')):
                original=extract_member(original,name,directory/('member-'+str(level)),config)
        return parse_uploaded_pdf(original, source, output, config)


def parse_uploaded_pdf(path, source, output, config):
    import pymupdf as fitz
    validate_source(source)
    if path.stat().st_size > min(config.max_download_bytes, 200*1024*1024):
        raise ValueError('PDF 超过 MinerU 上传大小限制')
    with fitz.open(path) as document:
        if len(document) > 200:
            raise ValueError('MinerU API 单文件最多支持 200 页，请先拆分公开资料')
    deadline = time.monotonic()+config.mineru_timeout
    try:
        # The Token belongs only to mineru.net, never signed object URLs.
        with httpx.Client(base_url=API, headers={'Authorization': 'Bearer '+config.mineru_api_key}, timeout=60) as api, \
             httpx.Client(timeout=60) as storage:
            def request(method, endpoint, **kwargs):
                response = api.request(method, endpoint, **kwargs)
                response.raise_for_status()
                data = response.json()
                if data.get('code') != 0:
                    raise ValueError('MinerU API 拒绝了请求，请检查 Token、额度和文件限制')
                return data['data']
            batch = request('POST', 'file-urls/batch', json={
                'files': [{'name': 'course.pdf', 'data_id': 'course-file'}],
                'model_version': config.mineru_api_model, 'enable_formula': True, 'enable_table': True, 'language': 'ch'})
            batch_id = batch['batch_id']
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', batch_id):
                raise ValueError('MinerU 任务编号格式不正确')
            with path.open('rb') as file:
                response = storage.put(public_url(batch['file_urls'][0]), content=file)
                response.raise_for_status()
            while time.monotonic() < deadline:
                result = request('GET', 'extract-results/batch/'+batch_id)['extract_result']
                if len(result) != 1:
                    raise ValueError('MinerU 返回的文件结果数量不正确')
                item = result[0]
                if item['state'] == 'failed':
                    raise ValueError('MinerU 文件解析失败，请检查服务额度、文件格式或重试')
                if item['state'] == 'done':
                    with storage.stream('GET', public_url(item['full_zip_url'])) as response:
                        response.raise_for_status()
                        chunks, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > config.max_download_bytes:
                                raise ValueError('MinerU 下载结果超过大小限制')
                            chunks.append(chunk)
                    content = normalize_bundle(b''.join(chunks), config)
                    output.mkdir(parents=True, exist_ok=True)
                    notes = [f'MinerU 在线 {config.mineru_api_model} 提取；原图来自公开来源 PDF，尚未逐题核对']
                    return parsed_content(path, source, output, content, notes)
                time.sleep(min(2, max(0, deadline-time.monotonic())))
            raise ValueError('MinerU 在线解析等待超时，请稍后重试')
    except httpx.HTTPError as exc:
        code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else '连接'
        raise ValueError(f'MinerU API 请求失败（{code}），请检查 Token、额度或网络') from None
    except (KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile):
        raise ValueError('MinerU API 返回格式无法识别，未导入题库') from None
