import json
import sqlite3

import pytest

from aitown.config import Settings
from aitown.ui.demo import settings_for_ui
from aitown.ui.service import UIService
from test_ui import ask,session


def test_wechat_copy_keeps_personal_sources_and_omits_general_and_old_history(tmp_path):
    original=settings_for_ui(Settings(),tmp_path/'original')
    service=UIService(original,tmp_path/'original/ui.sqlite3')
    try:
        sid=session(service);ask(service,sid,'计算6*7')
    finally:service.close()
    cfg=settings_for_ui(original,tmp_path/'wechat',data='wechat')
    with sqlite3.connect(original.retrieval_database) as one, sqlite3.connect(cfg.retrieval_database) as two:
        assert one.execute('SELECT count(*) FROM chat_turns').fetchone()[0]>0
        assert two.execute('SELECT count(*) FROM chat_turns').fetchone()[0]==0
        assert one.execute("SELECT count(*) FROM knowledge_documents WHERE purpose='knowledge'").fetchone()[0]==2
        assert two.execute("SELECT count(*) FROM knowledge_documents WHERE purpose='knowledge'").fetchone()[0]==0
        for table in ['message_refs','memories','style_examples','facts']:
            assert one.execute('SELECT * FROM '+table).fetchall()==two.execute('SELECT * FROM '+table).fetchall()
        assert two.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert cfg.style_scope=='synthetic' and not cfg.knowledge_dir.exists()
    copied=UIService(cfg,tmp_path/'wechat/ui.sqlite3',data='wechat')
    try:
        sid=session(copied);reply=ask(copied,sid,'我喜欢咖啡还是茶？')
        assert reply['sources'] and '茶' in reply['answer']
        assert copied.call('_bootstrap')['data_label']=='微信资料副本'
        maintenance=copied.call('_maintenance',sid)
        assert not maintenance['documents'] and maintenance['memories']
        with sqlite3.connect(cfg.retrieval_database) as conn:
            with conn:conn.execute("UPDATE message_refs SET review_status='pending' WHERE direction='outgoing'")
        assert copied.call('_maintenance',sid)['memories']==[]
    finally:copied.close()
    # Reopening the existing copy preserves its conversations rather than cloning again.
    settings_for_ui(original,tmp_path/'wechat',data='wechat')
    with sqlite3.connect(cfg.retrieval_database) as conn:assert conn.execute('SELECT count(*) FROM chat_turns').fetchone()[0]==1
    assert json.loads((tmp_path/'wechat/origin.json').read_text(encoding='utf-8'))['kind']=='reviewed_wechat_copy'


def test_wechat_copy_rejects_formal_database_as_destination(tmp_path):
    original=settings_for_ui(Settings(),tmp_path)
    with pytest.raises(ValueError,match='分开'):settings_for_ui(original,tmp_path,data='wechat')


def test_generic_followup_uses_resolved_event_for_retrieval(tmp_path):
    cfg=settings_for_ui(Settings(),tmp_path)
    ui=UIService(cfg,tmp_path/'ui.sqlite3')
    try:
        sid=session(ui)
        ask(ui,sid,'查询我的记忆，主题：分享。重点看当时准备整理分享材料的计划')
        followup=ask(ui,sid,'这件事，我提过哪些相关内容？')
        assert followup['sources'] and '分享材料' in followup['answer']
        assert [s['name'] for s in followup['skills']]==['memory_query']
        assert followup['debug']['references'][0]['turn_ids']
        assert followup['debug']['decision']['retrieval_query']=='准备整理分享材料的计划'
        from aitown.nodes.references import retrieval_focus
        from aitown.models import ReferenceResolution
        refs=[ReferenceResolution(kind='event',expression='这件事',selected='准备整理分享材料的计划')]
        assert retrieval_focus('这件事，我去过深圳吗？',refs) is None
    finally:ui.close()
