"""Create an editable local demonstration copy of the reviewed personal corpus."""
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from ..config import Settings


def settings_for_wechat(installed, directory, *, mode='demo', scope=''):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    source=installed.retrieval_database
    if source is None or not source.is_file():raise ValueError('微信演示需要已配置的个人应用库。')
    database=directory/'application.sqlite3'
    if database.resolve()==source.resolve():raise ValueError('微信演示目录必须与正式应用库分开。')
    profile=directory/'profile.json'
    if not database.exists():
        temporary=directory/'application.new.sqlite3'
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as original, closing(sqlite3.connect(temporary)) as copied:
            original.backup(copied)
            copied.execute('PRAGMA foreign_keys=ON')
            with copied:
                # Only the new demonstration copy is edited. Keep all personal
                # sources and train/dev/test labels; discard old CLI histories
                # and unrelated synthetic/general TXT/MD documents.
                copied.execute('DELETE FROM runtime_runs')
                copied.execute('DELETE FROM chat_sessions')
                copied.execute('DELETE FROM runtime_legacy_imports')
                copied.execute("DELETE FROM knowledge_documents WHERE purpose='knowledge'")
        temporary.replace(database)
        (directory/'origin.json').write_text(json.dumps({'kind':'reviewed_wechat_copy','source_database':str(source),
            'created_at':datetime.now().astimezone().isoformat(),
            'note':'Personal sources and split labels retained; general documents and previous runtime history omitted.'},
            ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if not installed.style_profile or not installed.style_profile.is_file():raise ValueError('微信演示需要已配置的表达画像文件。')
    if not profile.exists():shutil.copyfile(installed.style_profile,profile)
    if not scope:
        with closing(sqlite3.connect(database)) as conn:
            row=conn.execute("SELECT e.conversation_id FROM style_examples s JOIN episodes e ON e.id=s.episode_id WHERE s.split='train' GROUP BY e.conversation_id ORDER BY count(*) DESC,e.conversation_id LIMIT 1").fetchone()
        scope=installed.style_scope or (row[0] if row else '')
    return Settings.model_validate({**installed.model_dump(),'api_key':installed.api_key,
        'question_api':installed.question_api,'mineru_api_key':installed.mineru_api_key,'mode':mode,
        'runtime_database':database,'retrieval_database':database,'style_database':database,
        'style_profile':profile,'style_scope':scope,'memory_db':directory/'empty-legacy.sqlite3',
        'checkpoint_db':directory/'checkpoints.sqlite3','retrieval_index_dir':directory/'retrieval',
        'knowledge_dir':directory/'empty-knowledge','embedding_model':''})
