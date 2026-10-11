import json
import sqlite3
from datetime import datetime

import pytest

from aitown.knowledge import KnowledgeBase
from aitown.memory import MemoryStore, extract_batch
from aitown.memory.extract import TZ, extract_message
from aitown.memory.models import Memory
from aitown.memory.repository import review_record
from aitown.storage import Repository
from aitown.storage.index import StoredIndex
from aitown.storage.schema import DDL


def stamp(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=TZ).timestamp())


def message(text, outgoing=True, timestamp=None, mid=1):
    return dict(id=mid, batch_id="synthetic", source_id=mid, conversation_id="c0001", alias="c0001",
                timestamp=timestamp or stamp("2025-03-10T10:00:00"), direction="outgoing" if outgoing else "incoming",
                speaker_id="self" if outgoing else "other-c0001", type=1, status="text", content=text, review_status="approved")


def populated(tmp_path, messages, splits=None):
    r = Repository(tmp_path / "app.sqlite3")
    with r.conn:
        r.conn.execute("INSERT INTO import_batches VALUES('synthetic','test',0,NULL,NULL,'ready')")
        for i, m in enumerate(messages, 1):
            m["id"] = m["source_id"] = i
            r.conn.execute("INSERT INTO message_refs VALUES(:id,:batch_id,:source_id,:conversation_id,:alias,:timestamp,:direction,:speaker_id,:type,:status,:content,:review_status)", m)
            r.conn.execute("INSERT INTO episodes VALUES(?, 'synthetic', ?, 'c0001', ?, ?, '[]')", (i, f"e{i}", m["timestamp"], m["timestamp"]))
            r.conn.execute("INSERT INTO episode_messages VALUES(?,?,0)", (i,i))
            if splits:
                r.conn.execute("INSERT INTO style_examples VALUES(?, 'synthetic', ?, ?, 'test', ?, '[]', ?, '[]')", (i,f"s{i}",i,splits[i-1],m["content"]))
    return r


@pytest.mark.parametrize("text,outgoing,category,subject", [
    ("我喜欢咖啡",True,"preference","self"),
    ("我喜欢咖啡",False,"other_person","other:c0001"),
    ("他说我喜欢咖啡",True,"unknown","unknown"),
    ('我喜欢“咖啡”',True,"unknown","unknown"),
    ("我喜欢加班哈哈",True,"unknown","unknown"),
    ("我喜欢加班🙃",True,"unknown","unknown"),
    ("hx的生日是3月12日",True,"other_person","other:c0001:hx"),
    ("我的生日是2月30日",True,"unknown","unknown"),
    ("我是计算机专业的学生",True,"long_term","self"),
    ("我明天去图书馆",True,"plan","self"),
    ("我昨天去了图书馆",True,"event","self"),
    ("我没完成了作业",True,"unknown","unknown"),
    ("我今天去学校咨询 遇到了学长",True,"unknown","unknown"),
    ("没有没有 我参加了机器人计划",True,"event","self"),
    ("而且我个人对代码也不是很感冒",True,"preference","self"),
])
def test_speaker_report_joke_birthday_and_tense(text,outgoing,category,subject):
    m=extract_message(message(text,outgoing),1,"train")
    assert (m.category,m.subject)==(category,subject)
    assert m.status != "confirmed"


def test_relative_time_and_no_forced_fact():
    negative=extract_message(message("而且我个人对代码也不是很感冒"),1,"train")
    assert negative.value=="不感兴趣：代码"
    m=extract_message(message("我明天去图书馆"),1,"train")
    assert m.occurred_at==stamp("2025-03-11T00:00:00")
    assert extract_message(message("我下周去图书馆"),1,"train").occurred_at is None
    assert extract_message(message("我今年五一刚去过"),1,"train").occurred_at is None
    assert extract_message(message("对的"),1,"train") is None
    with pytest.raises(ValueError):
        Memory(entry_key="x",category="inference",basis="inferred",status="confirmed",recorded_at=0)


def test_v1_migration_preserves_existing_records(tmp_path):
    path=tmp_path/"v1.sqlite3"
    conn=sqlite3.connect(path)
    conn.executescript(DDL+"\nPRAGMA user_version=1;")
    conn.execute("INSERT INTO chat_sessions VALUES('kept','anotherme')")
    conn.commit()
    conn.close()
    r=Repository(path)
    assert r.conn.execute("PRAGMA user_version").fetchone()[0]==4
    assert r.conn.execute("SELECT id FROM chat_sessions").fetchone()[0]=="kept"
    assert r.conn.execute("PRAGMA foreign_key_check").fetchall()==[]
    r.close()


def test_review_temporal_queries_idempotence_and_index(tmp_path):
    old=stamp("2025-03-10T10:00:00")
    new=stamp("2025-04-10T10:00:00")
    r=populated(tmp_path,[message("我喜欢咖啡",timestamp=old),message("我现在喜欢茶",timestamp=new)],["train","train"])
    entries=[m for m in extract_batch(r) if m.category=="preference"]
    reviews={m.entry_key:review_record(m) for m in entries}
    store=MemoryStore(r)
    store.save(entries[:1],reviews)
    index=StoredIndex(r,"fact")
    assert index.search("咖啡",threshold=0.03)
    store.save(entries,reviews)
    assert [m.value for m in store.query(as_of=old+1)]==["喜欢咖啡"]
    assert [m.value for m in store.query(as_of=new+1)]==["喜欢茶"]
    assert r.query_facts("咖啡")==[]
    assert all(h["text"]=="喜欢茶" for h in index.search("咖啡"))
    counts=r.counts()
    assert store.save(entries,reviews)==0 and r.counts()==counts
    assert len(store.query(include_expired=True))==2
    r.close()


def test_inference_and_other_and_holdouts_are_separate(tmp_path):
    r=populated(tmp_path,[message("今天做作业"),message("昨天做作业"),message("聊聊作业"),message("我喜欢咖啡",False),message("我喜欢茶")],["train","train","train","train","test"])
    entries=extract_batch(r)
    store=MemoryStore(r)
    reviews={m.entry_key:review_record(m) for m in entries if m.basis=="direct"}
    store.save(entries,reviews)
    assert store.query("作业")==[]
    assert len(store.query("作业",basis="inferred",include_candidates=True))==1
    assert store.query("茶")==[]
    assert store.query("咖啡")==[]
    assert store.query("咖啡",subject="other:c0001")
    assert r.query_facts("")==[] and r.query_traits("")==[]
    r.close()


def test_stale_review_and_modified_source_rollback(tmp_path):
    r=populated(tmp_path,[message("我喜欢茶"),message("我明天去图书馆")],["train","train"])
    entries=extract_batch(r)
    reviews={m.entry_key:review_record(m) for m in entries}
    reviews[entries[-1].entry_key]["value"]="wrong interpretation"
    with pytest.raises(ValueError): MemoryStore(r).save(entries,reviews)
    assert r.counts()["memories"]==0 and r.counts()["facts"]==0
    with r.conn: r.conn.execute("UPDATE message_refs SET content='changed' WHERE id=1")
    with pytest.raises(ValueError): MemoryStore(r).save(entries)
    r.close()


def test_export_import_and_source_deletion(tmp_path):
    r=populated(tmp_path,[message("我喜欢咖啡"),message("我明天去图书馆")],["train","test"])
    entries=extract_batch(r)
    store=MemoryStore(r)
    store.save(entries,{m.entry_key:review_record(m) for m in entries})
    output=tmp_path/"knowledge"/"memory.md"
    store.export(output)
    text=output.read_text(encoding="utf-8")
    assert "synthetic/1" in text and "图书馆" not in text
    assert KnowledgeBase(output.parent,threshold=0.03).search("咖啡")
    index=StoredIndex(r,"document")
    assert index.search("咖啡",threshold=0.03)
    assert r.delete_source(1)
    assert index.search("咖啡",threshold=0.03)==[]
    assert store.query("咖啡")==[]
    assert not r.conn.execute("SELECT 1 FROM memories WHERE entry_key LIKE '%:1'").fetchall()
    store.export(output)
    assert "咖啡" not in output.read_text(encoding="utf-8")
    r.close()
