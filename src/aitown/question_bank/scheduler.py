"""Launch only the configured course worker; no personal database is passed."""
import os
import json
import subprocess
import sys
import time

from .jobs import create_job, pid_alive
from .models import COURSE_ID, COURSE_NAME
from .repository import BankRepository


def launch(config, course=COURSE_ID, *, update=False, max_files=None, source_ids=None):
    with BankRepository(config.database) as repo:
        cid = repo.course_id(course)
        registered=repo.conn.execute('SELECT 1 FROM courses WHERE id=?',(cid,)).fetchone()
        if not registered and cid!=COURSE_ID:
            raise ValueError('未找到该课程的公开资料，请刷新课程目录后选择已有课程')
        if not registered:
            repo.save_discovery([{'id': COURSE_ID, 'name': COURSE_NAME, 'path':'public/resources/'+COURSE_NAME}], [])
        active = repo.conn.execute("SELECT * FROM jobs WHERE course_id=? AND status IN ('queued','running')", (cid,)).fetchone()
        if active and (pid_alive(active['owner_pid']) or (active['status']=='queued' and time.time()-active['updated_at']<30)):
            return {'job_id': active['id'], 'reused': True,
                    'queued': bool(source_ids and set(source_ids)-set(json.loads(active['source_ids'])))}
        if active:
            with repo.conn:
                repo.conn.execute("UPDATE jobs SET status='interrupted',owner_pid=NULL WHERE id=?", (active['id'],))
        job_id = create_job(repo, cid, max_files=max_files, source_ids=source_ids, update=update)
        if not repo.sources(cid):
            with repo.conn:
                repo.conn.execute("UPDATE jobs SET stage='discover' WHERE id=?", (job_id,))
        directory = config.root / 'jobs'
        directory.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        source_root = str(__import__('pathlib').Path(__file__).resolve().parents[2])
        env['PYTHONPATH'] = source_root+os.pathsep+env.get('PYTHONPATH','')
        # Carry the chosen parser into the worker without secrets in argv/logs.
        env.update(QUESTION_EXTRACTOR=config.extractor, MINERU_API_MODEL=config.mineru_api_model,
            MINERU_TIER=config.mineru_tier, QUESTION_PDF_OCR=config.pdf_ocr,
            QUESTION_OCR_COLUMNS=str(config.ocr_columns), MINERU_TIMEOUT=str(config.mineru_timeout),
            MINERU_PYTHON=str(config.mineru_python or ''), QUESTION_SIMILAR_PROVIDER='none')
        env['MINERU_API_KEY']=config.mineru_api_key if config.extractor=='mineru_api' else ''
        if config.similar_provider=='api':
            env['LOCAL_EMBEDDING_DIR']='';env['LOCAL_RERANKER_DIR']=''
        else:
            if config.embedding_dir:env['LOCAL_EMBEDDING_DIR']=str(config.embedding_dir)
            if config.reranker_dir:env['LOCAL_RERANKER_DIR']=str(config.reranker_dir)
        env.pop('QUESTION_API_KEY',None)
        command = [sys.executable,'-m','aitown.question_bank','--root',str(config.root.resolve()),'worker','--job-id',str(job_id),'--course',cid]
        try:
            with (directory/f'{job_id}.log').open('ab') as log:
                process=subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0, start_new_session=os.name!='nt')
            with repo.conn:
                repo.conn.execute("UPDATE jobs SET owner_pid=? WHERE id=? AND status='queued'",(process.pid,job_id))
        except OSError as exc:
            with repo.conn:
                repo.conn.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=?", (str(exc),time.time(),job_id))
            raise
        return {'job_id':job_id, 'reused':False,'worker_pid':process.pid}
