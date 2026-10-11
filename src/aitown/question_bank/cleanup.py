"""Remove processed raw cache only after its retained artifacts are readable."""
import json
import time
from pathlib import Path

from .repository import BankRepository


def _confined(path, directory):
    absolute=path.absolute()
    if absolute==directory or not absolute.is_relative_to(directory):
        return False
    if not absolute.resolve().is_relative_to(directory):
        return False
    for parent in (absolute,*absolute.parents):
        if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)():
            return False
        if parent==directory:
            return True
    return False


def _retention_reason(repo, source, root):
    if source['repository']=='user:upload':
        return '用户上传原件不可从公网恢复，保留本地文件'
    if source['error']:
        return '最近处理失败，保留原件供重试'
    if source['status'] in ('expanded','skipped'):
        return None
    if source['status'] not in ('ready','partial') or not source['processed_at']:
        return '尚未处理完成，保留原件供重试或审校'
    suffix=Path(source['member'] or source['path']).suffix.lower()
    pages=list(repo.conn.execute('SELECT image,location_kind FROM pages WHERE source_id=?',(source['id'],)))
    if suffix in ('.doc','.docx'):
        document=root/'processed'/str(source['id'])/'document.pdf'
        if not _confined(document,(root/'processed').resolve()) or not document.is_file() or not document.stat().st_size:
            return 'Word 转换 PDF 尚未保存'
        rendered=list(repo.conn.execute('SELECT image,location_kind FROM rendered_pages WHERE source_id=?',(source['id'],)))
        if rendered:
            pages=rendered
        elif suffix!='.doc' or any(p['location_kind']!='page' for p in pages):
            return 'Word 转换页记录尚未保存'
    if suffix in ('.pdf','.doc','.docx') and (not pages or any(not p['image'] for p in pages)):
        return '完整原页截图尚未保存'
    paths=[p['image'] for p in pages if p['image']]
    for question in repo.conn.execute('SELECT images FROM questions WHERE source_id=?',(source['id'],)):
        images=json.loads(question['images'])
        if suffix in ('.pdf','.doc','.docx') and not images:
            return '仍有题目缺少截图'
        paths.extend(images)
    for item in paths:
        path=Path(item)
        if not _confined(path,(root/'processed').resolve()) or not path.is_file() or not path.stat().st_size:
            return '题图或原页文件缺失，保留原件'
    if suffix in ('.pdf','.doc','.docx'):
        import pymupdf
        try:
            original=Path(source['local_path'])
            document=document if suffix in ('.doc','.docx') else original
            if document.is_file():
                with pymupdf.open(document) as pdf:
                    if not len(pdf) or len(pages)!=len(pdf):
                        return '原页记录与 PDF 页数不一致'
            for item in dict.fromkeys(paths):
                if not pymupdf.Pixmap(str(item)).samples:
                    return '题图或原页无法解码'
        except (OSError,ValueError,RuntimeError):
            return 'PDF 或截图无法读取，保留原件'
    return None


def cleanup_raw(config, course, *, job_id=None, keep_originals=False):
    root=config.root.resolve()
    raw=root/'raw'
    report={'deleted':[],'retained':[],'failed':[],'bytes_freed':0}
    with BankRepository(config.database) as repo:
        # Equality must be committed before an irreversible file deletion. If the
        # process stops between unlink and metadata update, retry remains safe.
        with repo.conn:
            repo.preserve_duplicates(course)
        # Serialize against new preparation jobs while changing cache metadata.
        repo.conn.execute('BEGIN IMMEDIATE')
        try:
            active=repo.conn.execute("SELECT id FROM jobs WHERE course_id=? AND status IN ('queued','running')",
                (repo.course_id(course),)).fetchall()
            if any(r['id']!=job_id for r in active):
                raise ValueError('课程准备任务正在运行，稍后再清理')
            sources=[s for s in repo.sources(course) if s['local_path']]
            candidates=[]
            for source in sources:
                path=Path(source['local_path'])
                if not _confined(path,raw):
                    reason='路径超出 raw 缓存或包含链接，未删除'
                elif keep_originals:
                    reason='本地持久化保留原件；仅显式 cleanup 才清理可恢复缓存'
                else:
                    reason=_retention_reason(repo,source,root)
                if reason:
                    report['retained'].append({'source_id':source['id'],'status':source['status'],'reason':reason})
                else:
                    candidates.append((source,path))
            for source,path in candidates:
                try:
                    size=path.stat().st_size if path.is_file() else 0
                    path.unlink(missing_ok=True)
                    repo.conn.execute('UPDATE sources SET local_path=NULL,raw_deleted_at=? WHERE id=?',
                        (time.time(),source['id']))
                    report['deleted'].append({'source_id':source['id'],'bytes':size})
                    report['bytes_freed']+=size
                    # Never recursively delete a directory; retained siblings survive.
                    parent=path.absolute().parent
                    while parent!=raw:
                        try:
                            parent.rmdir()
                        except OSError:
                            break
                        parent=parent.parent
                except OSError as exc:
                    report['failed'].append({'source_id':source['id'],'error':f'{type(exc).__name__}: {exc}'})
            repo.conn.commit()
        except BaseException:
            repo.conn.rollback()
            raise
    return report
