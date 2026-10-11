"""Fictional, source-labelled UI fixtures; never read the personal corpus."""
import json
import time
from pathlib import Path

from ..config import Settings
from ..memory import MemoryStore
from ..memory.models import Evidence, Memory
from ..memory.repository import review_record
from ..runtime import RuntimeStore, UserStatement
from ..storage import Repository
from ..style import ProfileStore


def settings_for_ui(installed, directory, *, data='synthetic', mode='demo', scope=''):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    if data=='wechat':
        from .wechat import settings_for_wechat
        return settings_for_wechat(installed,directory,mode=mode,scope=scope)
    if data=='configured':
        return Settings.model_validate({**installed.model_dump(),'api_key':installed.api_key,
            'question_api':installed.question_api,'mineru_api_key':installed.mineru_api_key,'mode':mode,'style_scope':scope})
    database=directory/'application.sqlite3'
    seed_demo(database,directory/'profile.json')
    return Settings.model_validate({**installed.model_dump(),'api_key':installed.api_key,
        'question_api':installed.question_api,'mineru_api_key':installed.mineru_api_key,'mode':mode,
        'runtime_database':database,'retrieval_database':database,'style_database':database,
        'style_profile':directory/'profile.json','style_feedback':None,'style_scope':'synthetic',
        'memory_db':directory/'empty-legacy.sqlite3','checkpoint_db':directory/'checkpoints.sqlite3',
        'retrieval_index_dir':directory/'retrieval','knowledge_dir':directory/'empty-knowledge',
        'source_scope':'synthetic','embedding_model':''})


def seed_demo(database, profile_path):
    repo=Repository(database)
    try:
        if repo.conn.execute("SELECT 1 FROM import_batches WHERE id='ui-fictional' AND status='ready'").fetchone():
            if not Path(profile_path).exists():
                ProfileStore.write(ProfileStore(repo).apply(ProfileStore(repo).generate()),profile_path)
            return
        now=int(time.time())
        pairs=[('下课一起去便利店吗？','好的！','agree'),
               ('这周末还参加露营吗？','周末的露营我就不参加了','refuse'),
               ('你知道那个新功能吗？','不知道 还没了解过','uncertain'),
               ('准备好分享材料了吗？','好滴\n我先整理一下','task')]
        with repo.conn:
            repo.conn.execute("INSERT OR IGNORE INTO import_batches VALUES('ui-fictional','synthetic',?,NULL,NULL,'importing')",(now,))
            for ordinal,(question,answer,scene) in enumerate(pairs):
                repo.conn.execute("INSERT OR IGNORE INTO episodes(batch_id,source_id,conversation_id,start,end,quality) VALUES('ui-fictional',?,'synthetic',?,?,'[]')",
                    (f'pair-{ordinal}',now-100+ordinal*2,now-99+ordinal*2))
                episode=repo.conn.execute("SELECT id FROM episodes WHERE batch_id='ui-fictional' AND source_id=?",(f'pair-{ordinal}',)).fetchone()[0]
                mids=[]
                for role,text in [('incoming',question),('outgoing',answer)]:
                    source_id=ordinal*2+(1 if role=='incoming' else 2)
                    repo.conn.execute("INSERT OR IGNORE INTO message_refs(batch_id,source_id,conversation_id,alias,timestamp,direction,speaker_id,type,status,content,review_status) VALUES('ui-fictional',?,'synthetic','合成角色',?,?,?,1,'text',?,'approved')",
                        (source_id,now-100+source_id,role,'self' if role=='outgoing' else 'peer:demo',text))
                    mid=repo.conn.execute("SELECT id FROM message_refs WHERE batch_id='ui-fictional' AND source_id=?",(source_id,)).fetchone()[0]
                    repo.conn.execute('INSERT OR IGNORE INTO episode_messages VALUES(?,?,?)',(episode,mid,len(mids)))
                    mids.append(mid)
                context=json.dumps([{'role':'user','content':question,'source_ids':[mids[0]]}],ensure_ascii=False)
                repo.conn.execute("INSERT OR IGNORE INTO style_examples(batch_id,source_id,episode_id,scene,split,context,target,quality) VALUES('ui-fictional',?,?,?,'train',?,?,'[]')",
                    (f'style-{ordinal}',episode,scene,context,answer))
                style=repo.conn.execute("SELECT id FROM style_examples WHERE batch_id='ui-fictional' AND source_id=?",(f'style-{ordinal}',)).fetchone()[0]
                repo.conn.executemany('INSERT OR IGNORE INTO style_sources VALUES(?,?,?,0)',[(style,mids[0],'context'),(style,mids[1],'target')])
        runtime=RuntimeStore(repo)
        if not repo.conn.execute("SELECT 1 FROM source_records WHERE session_id='fixture' AND scope='synthetic'").fetchone():
            runtime.record_statement(UserStatement(attribute='preference',value='不喜欢咖啡，喜欢茶',scope='synthetic'),
                '合成设定：我不喜欢咖啡，喜欢茶','fixture','anotherme')
        source=repo.conn.execute("SELECT * FROM message_refs WHERE direction='outgoing' ORDER BY id DESC LIMIT 1").fetchone()
        evidence=Evidence(message_id=source['id'],batch_id='ui-fictional',source_id=source['source_id'],
            timestamp=source['timestamp'],direction='outgoing',speaker_id='self',excerpt=source['content'])
        memory=Memory(entry_key='ui-sharing-plan',episode_id=episode,category='plan',subject='self',basis='direct',status='confirmed',
            scope='synthetic',split='train',recorded_at=now,summary='计划整理分享材料，尚未完成',evidence=[evidence])
        MemoryStore(repo).save([memory],{memory.entry_key:review_record(memory)})
        repo.upsert_document('青竹活动室.md','合成资料：青竹活动室最多容纳9人，预约须提前两天，每位参加者材料费18元。')
        repo.upsert_document('云杉花园.md','合成资料：云杉花园周一闭园，其他日期09:00至17:00开放。')
        profile=ProfileStore(repo).apply(ProfileStore(repo).generate())
        ProfileStore.write(profile,profile_path)
        with repo.conn:repo.conn.execute("UPDATE import_batches SET status='ready' WHERE id='ui-fictional'")
    finally:repo.close()
