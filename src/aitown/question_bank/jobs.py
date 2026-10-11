"""One independent worker per course, durable progress and explicit recovery."""
import json
import os
import sqlite3
import time
from pathlib import Path, PurePosixPath

from .models import BankConfig
from .prepare import parse_file
from .repository import BankRepository, encode
from .sources import GithubSource
from .archives import ARCHIVE_SUFFIXES, expand_archive, restore_source


def pid_alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def create_job(repo, course, *, max_files=None, source_id=None, source_ids=None, update=False):
    cid = repo.course_id(course)
    if not repo.conn.execute('SELECT 1 FROM courses WHERE id=?', (cid,)).fetchone():
        raise ValueError('课程未发现，请先 discover')
    rows = [r for r in repo.sources(cid) if not r['remote_removed'] and (r['id'] in source_ids if source_ids is not None else r['id'] == source_id if source_id else not r['member'])]
    if source_ids is not None and {r['id'] for r in rows} != set(source_ids):
        raise ValueError('补充资料不属于该课程')
    if source_id is not None and not rows:
        raise ValueError('来源不属于该课程')
    if not update and source_id is None and source_ids is None:
        rows = [r for r in rows if r['status'] not in ('ready', 'partial', 'unsupported', 'skipped', 'expanded','needs_review')]
    if max_files is not None:
        rows = rows[:max_files]
    # The unique active-course index arbitrates concurrent requests, not a filesystem gate.
    with repo.conn:
        try:
            cursor = repo.conn.execute('INSERT INTO jobs(course_id,status,stage,source_ids,updated_at,options) VALUES(?,?,?,?,?,?)',
                (cid, 'queued', 'queued-update' if update else 'queued', encode([r['id'] for r in rows]), time.time(),
                 encode({'update':update,'max_files':max_files,'source_id':source_id,'source_ids':source_ids})))
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return repo.conn.execute("SELECT id FROM jobs WHERE course_id=? AND status IN ('queued','running')", (cid,)).fetchone()[0]


def claim(repo, job_id, *, resume=False):
    repo.conn.execute('BEGIN IMMEDIATE')
    try:
        row = repo.conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise ValueError('准备任务不存在')
        if row['status'] == 'running' and pid_alive(row['owner_pid']):
            raise ValueError('课程准备任务已有运行中的 worker')
        if row['status'] != 'queued' and not resume:
            raise ValueError('已有任务须使用 resume 继续')
        repo.conn.execute("UPDATE jobs SET status='running',owner_pid=?,updated_at=? WHERE id=?", (os.getpid(), time.time(), job_id))
        repo.conn.commit()
        return dict(row)
    except BaseException:
        repo.conn.rollback()
        raise


def expand_zip(repo, source, path, config):
    return expand_archive(repo,source,path,config)


def run_job(config: BankConfig, job_id, *, resume=False, progress=print, build_vectors=False):
    repo = BankRepository(config.database)
    remote = GithubSource(config)
    try:
        job = claim(repo, job_id, resume=resume)
        ids, done = json.loads(job['source_ids']), json.loads(job['done_ids'])
        options = json.loads(job.get('options','{}'))
        update = options.get('update',job['stage'] == 'queued-update')
        catalog_sources = [s for s in repo.sources(job['course_id']) if not s['repository'].startswith('user:')]
        if job['stage'] in ('discover','queued-update') and options.get('source_ids') is None and (catalog_sources or not ids):
            try:
                courses, files = remote.discover()
            except Exception as exc:
                with repo.conn:
                    repo.conn.execute('UPDATE courses SET discovery_error=? WHERE id=?',
                        (f'{type(exc).__name__}: {exc}'[:500],job['course_id']))
                raise
            repo.save_discovery(courses, files)
            ids = [s['id'] for s in repo.sources(job['course_id']) if not s['member'] and not s['remote_removed']]
            if options.get('source_id'):
                ids = [sid for sid in ids if sid == options['source_id']]
            if options.get('max_files'):
                ids = ids[:options['max_files']]
            with repo.conn:
                repo.conn.execute("UPDATE jobs SET source_ids=?,stage='download' WHERE id=?", (encode(ids),job_id))
        for sid in ids:
            if sid in done:
                continue
            source = repo.source(sid)
            with repo.conn:
                repo.conn.execute("UPDATE jobs SET current_source=?,stage='download',updated_at=? WHERE id=?", (sid, time.time(), job_id))
            progress(encode({'job_id': job_id, 'source_id': sid, 'stage': 'download', 'done': len(done), 'total': len(ids),
                'failed':sum(repo.source(completed)['status']=='failed' for completed in done)}))
            try:
                filename = PurePosixPath(source['member'] or source['path']).name
                if filename.startswith('~$'):
                    repo.set_source(sid, status='skipped', notes=encode(['Word 临时锁文件，不作为课程资料']))
                else:
                    path = restore_source(repo,source,config,remote,update=update and not source['member'])
                    source = repo.source(sid)
                    if path.suffix.lower() in ARCHIVE_SUFFIXES:
                        members = expand_archive(repo, source, path, config)
                        ids.extend(mid for mid in members if mid not in ids)
                        repo.set_source(sid, status='expanded', notes=encode([f'已解包 {len(members)} 个成员']))
                    else:
                        with repo.conn:
                            repo.conn.execute("UPDATE jobs SET stage='parse',updated_at=? WHERE id=?", (time.time(), job_id))
                        # Old records are excluded during a source update, including a failed parse.
                        repo.set_source(sid, status='processing')
                        local_config = config.model_copy(update={'extractor':'basic'}) if source['repository'].startswith('user:') else config
                        parsed = parse_file(path, source, config.root / 'processed' / str(sid),local_config)
                        repo.replace_parsed(sid, parsed)
            except Exception as exc:
                repo.set_source(sid, status='failed', error=f'{type(exc).__name__}: {exc}'[:1000])
            done.append(sid)
            with repo.conn:
                repo.conn.execute('UPDATE jobs SET source_ids=?,done_ids=?,updated_at=? WHERE id=?', (encode(ids), encode(done), time.time(), job_id))
        failures = sum(repo.source(sid)['status'] == 'failed' for sid in ids)
        index_result,index_error=None,None
        if build_vectors and config.embedding_dir and repo.coverage(job['course_id'])['questions']:
            with repo.conn:
                repo.conn.execute("UPDATE jobs SET stage='index',updated_at=? WHERE id=?",(time.time(),job_id))
            try:
                from .semantic import build_index
                index_result=build_index(config,course=job['course_id'])
            except Exception as exc:
                index_error=f'{type(exc).__name__}: {exc}'
        from .cleanup import cleanup_raw
        cleanup=cleanup_raw(config,job['course_id'],job_id=job_id,keep_originals=True)
        with repo.conn:
            repo.conn.execute('UPDATE jobs SET status=?,stage=?,error=?,owner_pid=NULL,current_source=NULL,updated_at=? WHERE id=?',
                ('partial' if failures or index_error or cleanup['failed'] else 'complete', 'index' if index_error else 'finished',index_error,time.time(), job_id))
        # An upload can arrive while this worker runs. Its ordinary source status
        # is the durable queue; failures wait for an explicit retry, never loop.
        pending = [r['id'] for r in repo.sources(job['course_id']) if r['repository'].startswith('user:')
                   and not r['remote_removed'] and r['status'] in ('discovered','downloaded') and r['id'] not in ids]
        if pending:
            from .scheduler import launch
            try:launch(config, job['course_id'], source_ids=pending)
            except Exception as exc:
                with repo.conn:
                    repo.conn.execute('UPDATE jobs SET error=? WHERE id=?', (f'追加资料未启动，请点击继续收集：{type(exc).__name__}',job_id))
        return {'job_id': job_id, 'done': len(done), 'failed': failures, 'coverage': repo.coverage(job['course_id']),
                'index':index_result,'index_error':index_error,'cleanup':cleanup}
    except BaseException as exc:
        # KeyboardInterrupt is durable. SIGKILL is recovered from the dead owner PID.
        if 'job' in locals():
            with repo.conn:
                repo.conn.execute("UPDATE jobs SET status='interrupted',owner_pid=NULL,error=?,updated_at=? WHERE id=?", (type(exc).__name__, time.time(), job_id))
        raise
    finally:
        remote.close()
        repo.close()
