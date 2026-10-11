import json
import sys
import threading
import time

import httpx
import pytest

from aitown.config import Settings
from aitown.ui.demo import settings_for_ui
from aitown.ui.server import make_server
from aitown.ui.service import UIService
from aitown.ui.wechat_sync import WechatSync


def message(local=1, *, content='我喜欢喝茶', own=True, db='message/message_0.db', server=None, **fields):
    return dict(local_id=local,server_id=server,source_db=db,real_sender_id=1,type='文本',type_code=1,
        sender='我' if own else '虚构同学',sender_username='wxid_fictional_self' if own else 'wxid_fictional_other',
        is_self=own,sender_status='resolved',time='2026-10-10 12:00:00',content=content,**fields)


def snapshot(path, rows, **changes):
    data=dict(exporter_version='1.3.5',chat_type='private',chat_name='虚构同学',message_count=len(rows),messages=rows)
    data.update(changes);path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8-sig')


@pytest.fixture
def sync(tmp_path):
    manager=WechatSync(tmp_path/'application.sqlite3',tmp_path/'temporary')
    path=tmp_path/'chat_full_parsed.json'
    sid=manager.save(dict(label='学习伙伴',chat_name='虚构同学',json_path=str(path)))['id']
    try:yield manager,path,sid
    finally:manager.close()


def approve(manager,sid,*,confirm=True,action='approve'):
    items=manager.preview(sid)['items']
    for item in items:item['confirm_memory']=bool(item['memory']) and confirm
    with manager.repo() as repo:return manager.review(repo,dict(id=sid,action=action,items=items))


def test_pending_review_memory_restart_and_scope_isolation(sync):
    manager,path,sid=sync
    snapshot(path,[message(),message(2,own=False,content='我喜欢喝咖啡')])
    assert manager.sync_once(sid)['added']==2
    with manager.repo() as repo:
        assert repo.conn.execute('SELECT COUNT(*) FROM facts').fetchone()[0]==0
        assert repo.conn.execute('SELECT COUNT(*) FROM memories').fetchone()[0]==0
    items=manager.preview(sid)['items']
    assert items[0]['memory']['subject']=='self' and items[1]['memory'] is None
    assert approve(manager,sid)=={'reviewed':2,'confirmed_memories':1}
    assert manager.sync_once(sid)['added']==0
    reopened=WechatSync(manager.database,manager.directory)
    try:
        assert reopened.status()['sources'][0]['counts']=={'approved':2}
        with reopened.repo() as repo:
            memory=repo.conn.execute('SELECT * FROM memories').fetchone()
            assert memory['scope']==f'wechat:{sid}' and memory['status']=='confirmed'
            assert repo.conn.execute("SELECT value FROM facts WHERE subject='self'").fetchone()[0]=='喜欢喝茶'
    finally:reopened.close()
    assert path.is_file() and list(manager.directory.iterdir())==[]


def test_shards_server_id_and_unknown_senders(sync):
    manager,path,sid=sync
    known=message(server='18446744073709551615')
    incoming=message(1,db='message/message_1.db',own=False)
    unknown=message(2);unknown.update(sender_status='unmapped_sender_id',sender='我',sender_username=None,is_self=None)
    image=message(3);image.update(type_code=3)
    snapshot(path,[known,incoming,unknown,image])
    assert manager.sync_once(sid)=={'added':2,'changed':0,'skipped':2}
    # Stable server IDs deduplicate re-sharded snapshots, even beyond SQLite int64.
    moved=message(9,db='message/message_2.db',server=known['server_id'])
    snapshot(path,[moved,incoming,unknown,image])
    assert manager.sync_once(sid)['added']==0
    assert len(manager.preview(sid)['items'])==2


def test_changed_sources_revoke_facts_and_require_current_review(sync):
    manager,path,sid=sync
    snapshot(path,[message()]);manager.sync_once(sid)
    stale=manager.preview(sid)['items'];approve(manager,sid)
    snapshot(path,[message(content='我喜欢喝咖啡')]);assert manager.sync_once(sid)['changed']==1
    with manager.repo() as repo:
        assert repo.conn.execute('SELECT COUNT(*) FROM facts').fetchone()[0]==0
        assert repo.conn.execute('SELECT COUNT(*) FROM memories').fetchone()[0]==0
        with pytest.raises(ValueError,match='正文发生变化'):
            manager.review(repo,dict(id=sid,action='approve',items=stale))
    approve(manager,sid)
    with manager.repo() as repo:assert repo.conn.execute('SELECT value FROM facts').fetchone()[0]=='喜欢喝咖啡'


def test_partial_wrong_account_and_key_conflicts_leave_database_unchanged(sync):
    manager,path,sid=sync
    snapshot(path,[message(server='100')]);manager.sync_once(sid)
    for rows,changes in [([message()],{'chat_name':'另一个会话'}),([message(),message()],{}),
        ([message(server='101')],{}),([dict(message(),sender_username='wxid_other_account')],{}),
        ([message()],{'message_count':2})]:
        snapshot(path,rows,**changes)
        with pytest.raises(ValueError):manager.sync_once(sid)
    path.write_text('{"messages":[',encoding='utf-8')
    with pytest.raises(ValueError,match='尚未写完'):manager.sync_once(sid)
    with manager.repo() as repo:
        assert repo.conn.execute('SELECT COUNT(*) FROM message_refs').fetchone()[0]==1
        assert repo.conn.execute('SELECT content FROM message_refs').fetchone()[0]=='我喜欢喝茶'


def test_redaction_rejection_tombstones_and_delete(sync,monkeypatch):
    manager,path,sid=sync
    monkeypatch.setitem(sys.modules,'pypinyin',None)
    snapshot(path,[message(content='我叫张三，电话13812345678，密码 abc123，虚构同学一起学习。')])
    manager.sync_once(sid);item=manager.preview(sid)['items'][0]
    content=item['expected']['content']
    assert all(secret not in content for secret in ('张三','13812345678','abc123','虚构同学'))
    approve(manager,sid,confirm=False,action='reject')
    manager.sync_once(sid);assert manager.preview(sid)['items']==[]
    with manager.repo() as repo:repo.delete_source(item['id'])
    manager.sync_once(sid);assert manager.preview(sid)['items']==[]
    manager.delete(sid)
    new=manager.save(dict(label='新来源',chat_name='虚构同学',json_path=str(path)))['id']
    assert new>sid and path.is_file()
    with manager.repo() as repo:assert repo.conn.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_review_transaction_rolls_back_source_and_interpretation(sync):
    manager,path,sid=sync
    snapshot(path,[message(),message(2,content='我喜欢跑步')]);manager.sync_once(sid)
    items=manager.preview(sid)['items']
    for item in items:item['confirm_memory']=True
    items[-1]['memory']['value']='不实内容'
    with manager.repo() as repo:
        with pytest.raises(ValueError,match='记忆解释'):manager.review(repo,dict(id=sid,action='approve',items=items))
        assert repo.conn.execute('SELECT COUNT(*) FROM episodes').fetchone()[0]==0
        assert repo.conn.execute('SELECT COUNT(*) FROM facts').fetchone()[0]==0
        assert {r[0] for r in repo.conn.execute('SELECT review_status FROM message_refs')}=={'pending'}


def test_background_progress_retry_stop_and_chat_responsiveness(tmp_path,monkeypatch):
    cfg=settings_for_ui(Settings(),tmp_path/'app');ui=UIService(cfg,tmp_path/'ui.sqlite3')
    manager=ui.wechat_sync;path=tmp_path/'chat.json'
    sid=manager.save(dict(label='学习',chat_name='虚构同学',json_path=str(path)))['id']
    entered=threading.Event();release=threading.Event();original=manager.sync_once
    def delayed(source):entered.set();release.wait(10);return original(source)
    monkeypatch.setattr(manager,'sync_once',delayed)
    try:
        manager.start(sid);assert entered.wait(5)
        assert ui.call('_bootstrap')['has_wechat_sync']
        assert manager.status()['sources'][0]['state']=='running'
        with pytest.raises(ValueError):manager.start(sid)
        with pytest.raises(ValueError):manager.delete(sid)
        manager.stop(sid);release.set()
        deadline=time.monotonic()+10
        while sid in manager.workers and time.monotonic()<deadline:time.sleep(.01)
        assert manager.status()['sources'][0]['state']=='failed'
        snapshot(path,[message()]);manager.start(sid)
        deadline=time.monotonic()+10
        while sid in manager.workers and time.monotonic()<deadline:time.sleep(.01)
        assert manager.status()['sources'][0]['counts']['pending']==1
    finally:release.set();ui.close()


def test_external_export_subprocess_without_shell_or_retained_raw_data(tmp_path):
    # A small external tool fixture exercises the actual subprocess protocol.
    tool=tmp_path/'工具 空格';tool.mkdir();path=tmp_path/'fixture.json'
    snapshot(path,[message()])
    (tool/'exporter_core.py').write_text("""APP_VERSION='1.3.5'
import shutil
from pathlib import Path
def export_chat(chat,out,db_dir=None):
    target=Path(out)/'chat_full_parsed.json';target.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).parent/'fixture.json',target)
    return {'json':str(target)}
""",encoding='utf-8')
    (tool/'fixture.json').write_bytes(path.read_bytes())
    manager=WechatSync(tmp_path/'app.sqlite3',tmp_path/'temporary')
    try:
        sid=manager.save(dict(label='学习',chat_name='虚构同学',mode='tool',tool_directory=str(tool),python_path=sys.executable))['id']
        assert manager.sync_once(sid)['added']==1
        assert list(manager.directory.iterdir())==[]
    finally:manager.close()


def test_periodic_failure_recovers_without_restarting(sync):
    manager,path,sid=sync
    class TwoRounds:
        stopped=False
        def is_set(self):return self.stopped
        def wait(self,seconds):
            assert seconds==300
            if not path.exists():snapshot(path,[message()]);return False
            self.stopped=True;return True
    manager._loop(sid,True,TwoRounds())
    status=manager.status()['sources'][0]
    assert status['state']=='idle' and status['error']=='' and status['counts']['pending']==1


def test_http_flow_review_retrieval_and_origin_protection(tmp_path):
    cfg=settings_for_ui(Settings(),tmp_path/'app');ui=UIService(cfg,tmp_path/'ui.sqlite3')
    server=make_server(ui,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    path=tmp_path/'fixture.json';snapshot(path,[message()])
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False) as client:
            assert client.get('/wechat-sync.js').status_code==200
            source=client.post('/api/wechat-source',json=dict(label='学习',chat_name='虚构同学',json_path=str(path))).json()
            sid=source['id']
            assert client.post('/api/wechat-start',json={'id':sid},headers={'Origin':'https://outside.example'}).status_code==403
            assert client.post('/api/wechat-start',json={'id':sid}).status_code==200
            deadline=time.monotonic()+10
            while sid in ui.wechat_sync.workers and time.monotonic()<deadline:time.sleep(.01)
            item=client.get(f'/api/wechat-preview?id={sid}').json()['items'][0];item['confirm_memory']=True
            job=client.post('/api/wechat-review',json=dict(id=sid,action='approve',items=[item])).json()['job_id']
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                result=client.get('/api/job',params={'id':job}).json()
                if result['status']!='running':break
                time.sleep(.01)
            assert result['status']=='complete',result
            session=ui.call('_create_session',{'scope':source['scope']})['id']
            reply=ui.call('_chat',{'session':session,'question':'我喜欢喝什么？'})['reply']
            assert '茶' in reply['answer'] and reply['sources']
            other=ui.call('_create_session',{'scope':'synthetic'})['id']
            other_reply=ui.call('_chat',{'session':other,'question':'我喜欢喝什么？'})['reply']
            assert all(e['id']!=item['id'] for s in other_reply['sources'] for e in s.get('evidence',[]))
    finally:server.shutdown();thread.join();server.server_close();ui.close()
