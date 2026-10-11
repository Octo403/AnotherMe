"""Locate retained course attachments without accepting arbitrary file paths."""
from pathlib import Path, PurePosixPath
import json


def local_source_file(root, source):
    root = Path(root).resolve()
    if source['local_path']:
        path = Path(source['local_path']).resolve()
        if path.is_relative_to(root/'raw') and path.is_file():
            return path, 'original'
    if Path(source['member'] or source['path']).suffix.lower() in ('.doc', '.docx'):
        path = (root/'processed'/str(source['id'])/'document.pdf').resolve()
        if path.is_relative_to(root/'processed') and path.is_file():
            return path, 'converted_pdf'
    raise FileNotFoundError('本地附件尚未保存或已清理，请重新收集该资料。')


def source_summary(root, source, question_count=0):
    try:
        _, kind = local_source_file(root, source)
    except FileNotFoundError:
        kind = None
    links=[]
    if kind and Path(source['member'] or source['path']).suffix.lower() in ('.html','.htm'):
        from .supplements import read_web
        try:_,links=read_web(local_source_file(root,source)[0],source.get('resolved_url') or source['url'])
        except (OSError,ValueError):pass
    return {key: source[key] for key in ('id', 'status', 'error', 'remote_removed')} | {
        'name': PurePosixPath(source['member'] or source['path']).name,
        'path': source['path'], 'member': source['member'], 'url': source['url'],
        'questions': question_count, 'local_available': kind is not None, 'file_kind': kind,
        'origin_kind': {'user:upload':'upload','user:url':'url'}.get(source['repository'],'catalog'),
        'notes': json.loads(source['notes']), 'links': links,
    }
