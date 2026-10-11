"""Local, opt-in snapshot sync for wechat-chat-export's parsed JSON.

The exporter is an optional external program. No WeChat keys, blobs, media or
raw transcripts are copied into the application database. Pending sources are
ineligible for retrieval; changed sources lose their previous derived records.
"""
import json
import os
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..memory.extract import extract_message
from ..memory.repository import MemoryStore, review_record
from ..storage import Repository
from ..wechat_clean import Redactor

DDL = '''
CREATE TABLE IF NOT EXISTS wechat_sync_sources(
 id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, chat_name TEXT NOT NULL,
 mode TEXT NOT NULL, json_path TEXT NOT NULL, tool_directory TEXT NOT NULL,
 python_path TEXT NOT NULL, db_directory TEXT NOT NULL, interval_seconds INTEGER NOT NULL,
 self_username TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'idle',
 phase TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', last_sync REAL,
 added INTEGER NOT NULL DEFAULT 0, changed INTEGER NOT NULL DEFAULT 0,
 skipped INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS wechat_sync_people(
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL REFERENCES wechat_sync_sources(id) ON DELETE CASCADE,
 username TEXT NOT NULL, UNIQUE(source_id,username));
CREATE TABLE IF NOT EXISTS wechat_sync_messages(
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL REFERENCES wechat_sync_sources(id) ON DELETE CASCADE,
 source_db TEXT NOT NULL, local_id TEXT NOT NULL, server_id TEXT,
 message_id INTEGER REFERENCES message_refs(id) ON DELETE SET NULL,
 UNIQUE(source_id,source_db,local_id));
CREATE UNIQUE INDEX IF NOT EXISTS wechat_sync_server_id
 ON wechat_sync_messages(source_id,server_id) WHERE server_id IS NOT NULL;
'''
MAX_JSON_BYTES = 100 * 1024 * 1024
ZONE = timezone(timedelta(hours=8))


def _identity(row):
    if row.get('sender_status') != 'resolved':
        # Unknown and system identities stay unknown, even if the label is "我".
        return 'unknown', 'unknown'
    username = row.get('sender_username')
    if not isinstance(username, str) or not username.strip():
        return 'unknown', 'unknown'
    if row.get('is_self') is True:return 'outgoing', 'self'
    if row.get('is_self') is False:return 'incoming', username
    return 'unknown', 'unknown'


def _timestamp(value):
    if not isinstance(value, str):raise ValueError('消息时间格式不正确。')
    try:return int(datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZONE).timestamp())
    except (ValueError, OverflowError):raise ValueError('消息时间格式不正确。') from None


def _message_key(row):
    local = row.get('local_id'); db = row.get('source_db')
    if isinstance(local, bool) or not isinstance(local, (str, int)) or not str(local).isdigit():
        raise ValueError('缺少稳定的 local_id，请使用 v1.3.5 的 JSON 导出。')
    if not isinstance(db, str) or not db or len(db)>500:raise ValueError('缺少 source_db，不能区分消息分片。')
    server = row.get('server_id')
    server = str(int(server)) if type(server) in (str, int) and str(server).isdigit() and int(server)>0 else None
    return db, str(int(local)), server


def read_snapshot(path, expected_chat):
    path = Path(path)
    if not path.is_file():raise ValueError('找不到 JSON 导出文件，请先在导出工具中导出所选会话。')
    if path.stat().st_size>MAX_JSON_BYTES:raise ValueError('JSON 超过 100 MiB，请缩小导出范围。')
    try:
        with path.open('r', encoding='utf-8-sig') as stream:data=json.load(stream)
    except (UnicodeError, json.JSONDecodeError):raise ValueError('JSON 尚未写完或格式不正确，请等导出完成后重试。') from None
    if not isinstance(data, dict) or data.get('exporter_version')!='1.3.5':
        raise ValueError('目前支持 wechat-chat-export v1.3.5 的 chat_full_parsed.json。')
    if data.get('chat_name')!=expected_chat:raise ValueError('导出会话名称与设置不一致，请检查所选文件和微信账号。')
    if data.get('chat_type') not in ('private','group'):raise ValueError('导出会话类型无效。')
    rows=data.get('messages')
    if not isinstance(rows,list) or type(data.get('message_count')) is not int or data['message_count']!=len(rows):
        raise ValueError('导出消息列表不完整。')
    # Validate the entire snapshot before any DB mutation, including non-text rows.
    keys=set(); servers=set(); selves=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('消息格式不正确。')
        db,local,server=_message_key(row)
        if (db,local) in keys or server and server in servers:raise ValueError('导出中有重复消息标识。')
        keys.add((db,local))
        if server:servers.add(server)
        _timestamp(row.get('time'))
        if type(row.get('type_code')) is not int or not isinstance(row.get('content'),str):
            raise ValueError('消息类型或正文格式不正确。')
        direction,_=_identity(row)
        if direction=='outgoing':selves.add(row['sender_username'])
    if len(selves)>1:raise ValueError('导出中出现多个本人账号，已停止合并。')
    return rows,next(iter(selves),'')


class WechatSync:
    def __init__(self, database, directory):
        self.database=Path(database);self.directory=Path(directory).resolve()
        self.directory.mkdir(parents=True,exist_ok=True)
        self.lock=threading.Lock();self.workers={};self.closed=False
        with self.repo() as repo:
            repo.conn.executescript(DDL)
            with repo.conn:repo.conn.execute("UPDATE wechat_sync_sources SET state='idle',phase='上次运行已结束，点击同步继续' WHERE state IN ('running','waiting','stopping')")

    @contextmanager
    def repo(self):
        repo=Repository(self.database)
        repo.conn.execute('PRAGMA busy_timeout=15000')
        try:yield repo
        finally:repo.close()

    def save(self, payload):
        mode=payload.get('mode','file')
        if mode not in ('file','tool'):raise ValueError('同步方式无效。')
        values={key:str(payload.get(key,'')).strip() for key in ('label','chat_name','json_path','tool_directory','python_path','db_directory')}
        for key in ('json_path','tool_directory','python_path','db_directory'):
            if values[key]:values[key]=str(Path(values[key]).expanduser().resolve())
        if not values['label'] or len(values['label'])>60:raise ValueError('请填写本地会话别名，最多 60 字。')
        if not values['chat_name'] or len(values['chat_name'])>200:raise ValueError('请填写导出中的准确会话名称。')
        try:interval=int(payload.get('interval_seconds',300))
        except (ValueError,TypeError):raise ValueError('同步间隔须为整数。') from None
        if not 30<=interval<=86400:raise ValueError('同步间隔须为 30–86400 秒。')
        if mode=='file' and not values['json_path']:raise ValueError('请填写 chat_full_parsed.json 的完整路径。')
        if mode=='tool':
            if not (Path(values['tool_directory'])/'exporter_core.py').is_file():raise ValueError('工具目录须包含 v1.3.5 的 exporter_core.py。')
            if not Path(values['python_path']).is_file():raise ValueError('请填写已安装工具依赖的 python.exe 完整路径。')
        with self.repo() as repo, repo.conn:
            cursor=repo.conn.execute('INSERT INTO wechat_sync_sources(label,chat_name,mode,json_path,tool_directory,python_path,db_directory,interval_seconds) VALUES(?,?,?,?,?,?,?,?)',
                tuple(values[k] for k in ('label','chat_name'))+(mode,)+tuple(values[k] for k in ('json_path','tool_directory','python_path','db_directory'))+(interval,))
            return {'id':cursor.lastrowid,'scope':f'wechat:{cursor.lastrowid}'}

    def status(self):
        with self.repo() as repo:
            sources=[]
            for row in repo.conn.execute('SELECT * FROM wechat_sync_sources ORDER BY id'):
                item=dict(row);item.pop('self_username')
                item['scope']=f"wechat:{row['id']}"
                item['counts']={r[0]:r[1] for r in repo.conn.execute('SELECT m.review_status,COUNT(*) FROM wechat_sync_messages w JOIN message_refs m ON m.id=w.message_id WHERE w.source_id=? GROUP BY m.review_status',(row['id'],))}
                sources.append(item)
            return {'sources':sources,'database':str(self.database.resolve()),'temporary_directory':str(self.directory)}

    def _source(self, repo, source_id):
        row=repo.conn.execute('SELECT * FROM wechat_sync_sources WHERE id=?',(int(source_id),)).fetchone()
        if not row:raise ValueError('微信同步来源不存在。')
        return dict(row)

    def _state(self, source_id, **fields):
        with self.repo() as repo, repo.conn:
            repo.conn.execute('UPDATE wechat_sync_sources SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),source_id))

    def start(self, source_id, repeat=False):
        if type(repeat) is not bool:raise ValueError('定时同步选项须为布尔值。')
        source_id=int(source_id)
        with self.lock:
            if self.closed:raise ValueError('本地服务正在关闭。')
            if source_id in self.workers:raise ValueError('该会话正在同步，请先停止。')
            with self.repo() as repo:self._source(repo,source_id)
            stop=threading.Event()
            worker=threading.Thread(target=self._loop,args=(source_id,repeat,stop),daemon=True,name=f'wechat-sync-{source_id}')
            self.workers[source_id]=(worker,stop)
            self._state(source_id,state='running',phase='准备同步',error='')
            worker.start()
        return {'started':True}

    def stop(self, source_id):
        source_id=int(source_id)
        with self.lock:
            current=self.workers.get(source_id)
            if current:
                current[1].set()
                self._state(source_id,state='stopping',phase='本轮导出结束后停止')
        return {'stopping':bool(current)}

    def _loop(self, source_id, repeat, stop):
        try:
            while not stop.is_set():
                try:self.sync_once(source_id)
                except Exception as exc:
                    error=str(exc) if isinstance(exc,ValueError) else f'本地同步失败（{type(exc).__name__}），请检查文件权限与工具依赖。'
                    self._state(source_id,state='failed',phase='同步失败',error=error[:500])
                    if not repeat:break
                if not repeat or stop.is_set():break
                with self.repo() as repo:interval=self._source(repo,source_id)['interval_seconds']
                self._state(source_id,state='waiting',phase=f'{interval} 秒后再次同步；失败时也会重试')
                if stop.wait(interval):break
            with self.repo() as repo:state=self._source(repo,source_id)['state']
            if state!='failed':self._state(source_id,state='idle',phase='已停止' if stop.is_set() else '同步完成，新增内容等待复核')
        finally:
            with self.lock:self.workers.pop(source_id,None)

    def _export(self, source, directory):
        self._state(source['id'],phase='正在调用本机微信导出工具（仅文本）')
        request=directory/'request.json';result=directory/'result.json'
        request.write_text(json.dumps({'chat':source['chat_name'],'out':str(directory/'snapshot'),'db':source['db_directory'],'result':str(result)}),encoding='utf-8')
        # Import by a configured source directory, never shell-expand user text.
        script="""import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from exporter_core import export_chat,APP_VERSION
if APP_VERSION!='1.3.5':raise RuntimeError('unsupported_exporter')
p=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
r=export_chat(p['chat'],p['out'],db_dir=p['db'] or None)
Path(p['result']).write_text(json.dumps({'json':r['json']}),encoding='utf-8')
"""
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        with (directory/'export.log').open('wb') as log:
            completed=subprocess.run([source['python_path'],'-I','-c',script,source['tool_directory'],str(request)],
                cwd=source['tool_directory'],stdout=log,stderr=log,creationflags=flags,check=False)
        if completed.returncode:
            # Upstream errors can contain contact names, keys and database paths.
            raise ValueError('微信导出失败。请确认 Windows 微信已登录、会话名称准确，工具版本为 1.3.5 且 Python 依赖已安装；也可先用工具手动导出，再选择导出文件同步。')
        path=Path(json.loads(result.read_text(encoding='utf-8'))['json']).resolve()
        if not path.is_relative_to(directory.resolve()):raise ValueError('导出工具返回了本次临时目录以外的文件。')
        return path

    def sync_once(self, source_id):
        with self.repo() as repo:source=self._source(repo,source_id)
        self._state(source_id,state='running',phase='读取导出快照',error='')
        # Only our own run directory is removed. User-selected export files survive.
        with tempfile.TemporaryDirectory(prefix='run-',dir=self.directory) as work:
            path=self._export(source,Path(work)) if source['mode']=='tool' else Path(source['json_path'])
            rows,self_username=read_snapshot(path,source['chat_name'])
            if source['self_username'] and self_username and source['self_username']!=self_username:
                raise ValueError('检测到微信本人账号变化，请为新账号新建同步来源。')
            self._state(source_id,phase=f'清洗和去重 {len(rows)} 条消息')
            return self._ingest(source,rows,self_username)

    def _ingest(self, source, rows, self_username):
        sid=source['id'];scope=f'wechat:{sid}';batch=f'wechat-sync:{sid}'
        added=changed=skipped=0
        with self.repo() as repo,repo.conn:
            repo.conn.execute("INSERT OR IGNORE INTO import_batches(id,source_kind,imported_at,status) VALUES(?,'wechat_sync',?,'ready')",(batch,time.time()))
            aliases={source['chat_name']:source['label']} if len(source['chat_name'])>=2 else {}
            people={r['username']:r['id'] for r in repo.conn.execute('SELECT * FROM wechat_sync_people WHERE source_id=?',(sid,))}
            for row in rows:
                username=row.get('sender_username')
                if isinstance(username,str) and username and username not in people:
                    cursor=repo.conn.execute('INSERT INTO wechat_sync_people(source_id,username) VALUES(?,?)',(sid,username));people[username]=cursor.lastrowid
                if username in people:
                    alias='本人' if row.get('is_self') is True else f'联系人{people[username]}'
                    aliases[username]=alias
                    name=row.get('sender')
                    if isinstance(name,str) and len(name)>=2 and name not in ('我','未知发送者','系统消息'):aliases[name]=alias
            redactor=Redactor(aliases)
            for row in sorted(rows,key=lambda r:(_timestamp(r['time']),str(r['local_id']))):
                db,local,server=_message_key(row)
                old=repo.conn.execute('SELECT * FROM wechat_sync_messages WHERE source_id=? AND source_db=? AND local_id=?',(sid,db,local)).fetchone()
                if not old and server:old=repo.conn.execute('SELECT * FROM wechat_sync_messages WHERE source_id=? AND server_id=?',(sid,server)).fetchone()
                if not old:
                    cursor=repo.conn.execute('INSERT INTO wechat_sync_messages(source_id,source_db,local_id,server_id) VALUES(?,?,?,?)',(sid,db,local,server))
                    external_id=cursor.lastrowid;mid=None
                else:
                    external_id=old['id'];mid=old['message_id']
                    if server and old['server_id'] and server!=old['server_id']:
                        raise ValueError('消息 local_id 对应的 server_id 已变化，请检查账号与导出来源。')
                    repo.conn.execute('UPDATE wechat_sync_messages SET source_db=?,local_id=?,server_id=? WHERE id=?',(db,local,server or old['server_id'],external_id))
                # Tombstones prevent periodic snapshots from reviving deleted records.
                if repo.conn.execute('SELECT 1 FROM deleted_sources WHERE batch_id=? AND source_id=?',(batch,external_id)).fetchone():skipped+=1;continue
                direction,speaker=_identity(row)
                if direction=='incoming':speaker=f'contact:{people[speaker]}'
                content,_=redactor.clean(row['content'])
                eligible=row['type_code']==1 and direction!='unknown' and bool(content) and len(content)<=50000
                existing=repo.conn.execute('SELECT * FROM message_refs WHERE id=?',(mid,)).fetchone() if mid else None
                if not eligible:
                    if existing:
                        repo._invalidate_sources([mid])
                        repo.conn.execute("UPDATE message_refs SET review_status='rejected',status='unsupported',content='' WHERE id=?",(mid,))
                    skipped+=1;continue
                fields={'timestamp':_timestamp(row['time']),'direction':direction,'speaker_id':speaker,'content':content}
                if existing and all(existing[k]==v for k,v in fields.items()) and existing['status']=='text':continue
                if existing:
                    repo._invalidate_sources([mid]);changed+=1
                    repo.conn.execute("UPDATE message_refs SET timestamp=?,direction=?,speaker_id=?,content=?,status='text',review_status='pending' WHERE id=?",(*fields.values(),mid))
                else:
                    cursor=repo.conn.execute("INSERT INTO message_refs(batch_id,source_id,conversation_id,alias,timestamp,direction,speaker_id,type,status,content,review_status) VALUES(?,?,?,?,?,?,?,1,'text',?,'pending')",(batch,external_id,scope,source['label'],*fields.values()))
                    mid=cursor.lastrowid;added+=1
                    repo.conn.execute('UPDATE wechat_sync_messages SET message_id=? WHERE id=?',(mid,external_id))
            repo.conn.execute('UPDATE wechat_sync_sources SET self_username=?,last_sync=?,added=?,changed=?,skipped=? WHERE id=?',(source['self_username'] or self_username,time.time(),added,changed,skipped,sid))
            return {'added':added,'changed':changed,'skipped':skipped}

    def preview(self, source_id, offset=0):
        with self.repo() as repo:
            source=self._source(repo,source_id);items=[]
            for r in repo.conn.execute("SELECT m.* FROM wechat_sync_messages w JOIN message_refs m ON m.id=w.message_id WHERE w.source_id=? AND m.review_status='pending' ORDER BY m.timestamp,m.id LIMIT 50 OFFSET ?",(int(source_id),max(0,int(offset)))):
                row=dict(r);row['review_status']='approved'
                memory=extract_message(row,None,'train')
                candidate=memory.model_dump() if memory and memory.subject=='self' and memory.basis=='direct' and memory.category not in ('unknown','inference') else None
                items.append({'id':r['id'],'expected':{k:r[k] for k in ('timestamp','direction','speaker_id','content')},'memory':candidate})
            return {'source':{'id':source['id'],'label':source['label'],'scope':f"wechat:{source['id']}"},'items':items}

    def library(self, source_id, offset=0):
        with self.repo() as repo:
            self._source(repo,source_id)
            rows=repo.conn.execute("SELECT m.id,m.timestamp,m.direction,m.speaker_id,m.content,e.episode_id FROM wechat_sync_messages w JOIN message_refs m ON m.id=w.message_id LEFT JOIN episode_messages e ON e.message_id=m.id WHERE w.source_id=? AND m.review_status='approved' ORDER BY m.timestamp,m.id LIMIT 50 OFFSET ?",(int(source_id),max(0,int(offset))))
            return {'items':[dict(row) for row in rows]}

    def review(self, repo, payload):
        source_id=int(payload.get('id',0));self._source(repo,source_id)
        decisions=payload.get('items');action=payload.get('action')
        if action not in ('approve','reject') or not isinstance(decisions,list) or not 1<=len(decisions)<=50:raise ValueError('请选择本页的 1–50 条资料进行复核。')
        memories=[]
        with repo.conn:
            # Hold the writer reservation across comparison and approval: a
            # background snapshot must not replace text between those steps.
            repo.conn.execute('BEGIN IMMEDIATE')
            seen=set()
            for decision in decisions:
                mid=int(decision.get('id',0))
                if mid in seen:raise ValueError('复核消息重复。')
                seen.add(mid)
                row=repo.conn.execute('SELECT m.* FROM message_refs m JOIN wechat_sync_messages w ON w.message_id=m.id WHERE w.source_id=? AND m.id=?',(source_id,mid)).fetchone()
                if not row or row['review_status']!='pending':raise ValueError('资料已改变或已处理，请刷新预览。')
                if {k:row[k] for k in ('timestamp','direction','speaker_id','content')}!=decision.get('expected'):raise ValueError('同步期间正文发生变化，请重新预览再复核。')
                repo.conn.execute('UPDATE message_refs SET review_status=? WHERE id=?',('approved' if action=='approve' else 'rejected',mid))
                if action=='reject':continue
                # A small source episode makes reviewed messages available to the
                # existing scoped dialogue_summary skill, without fabricating style.
                repo.conn.execute('INSERT INTO episodes(batch_id,source_id,conversation_id,start,end,quality) VALUES(?,?,?,?,?,?) ON CONFLICT(batch_id,source_id) DO UPDATE SET start=excluded.start,end=excluded.end',(row['batch_id'],str(row['source_id']),row['conversation_id'],row['timestamp'],row['timestamp'],'{}'))
                episode=repo.conn.execute('SELECT id FROM episodes WHERE batch_id=? AND source_id=?',(row['batch_id'],str(row['source_id']))).fetchone()[0]
                repo.conn.execute('INSERT OR IGNORE INTO episode_messages VALUES(?,?,0)',(episode,mid))
                approved=dict(row);approved['review_status']='approved'
                candidate=extract_message(approved,episode,'train')
                if decision.get('confirm_memory') is True:
                    if not candidate or candidate.subject!='self' or candidate.basis!='direct' or candidate.category in ('unknown','inference'):raise ValueError('该消息没有可确认的本人记忆。')
                    expected=candidate.model_dump();expected['episode_id']=None
                    if expected!=decision.get('memory'):raise ValueError('记忆解释已改变，请重新预览。')
                    memories.append(candidate)
            store=MemoryStore(repo)
            store.save(memories,{m.entry_key:review_record(m,note='user_reviewed_wechat_sync') for m in memories},within_transaction=True)
        return {'reviewed':len(decisions),'confirmed_memories':len(memories)}

    def delete(self, source_id):
        source_id=int(source_id)
        with self.lock:
            if source_id in self.workers:raise ValueError('请先停止同步，等待本轮导出结束后再删除。')
            with self.repo() as repo,repo.conn:
                repo.conn.execute('BEGIN IMMEDIATE')
                self._source(repo,source_id)
                ids=[r[0] for r in repo.conn.execute('SELECT message_id FROM wechat_sync_messages WHERE source_id=? AND message_id IS NOT NULL',(source_id,))]
                repo._invalidate_sources(ids)
                repo.conn.execute('DELETE FROM import_batches WHERE id=?',(f'wechat-sync:{source_id}',))
                repo.conn.execute('DELETE FROM wechat_sync_sources WHERE id=?',(source_id,))
        return {'deleted':True}

    def close(self):
        with self.lock:
            self.closed=True
            workers=list(self.workers.values())
            for _,stop in workers:stop.set()
        # Let upstream's finally clean its decryption workspace; never kill it.
        for worker,_ in workers:worker.join()
