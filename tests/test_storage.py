import json
import sqlite3
from pathlib import Path

import pytest

from aitown.storage import Repository
from aitown.storage.index import StoredIndex


def bundle(path):
    path.mkdir()
    messages = [{"id":i,"batch_id":"synthetic","conversation_id":"c0001","timestamp":1700000000+i,
                 "direction":"incoming" if i%2 else "outgoing","speaker_id":"other" if i%2 else "self",
                 "type":1,"status":"text","eligible_text":True,"content":"共同问题" if i%2 else "共同回答"} for i in range(1,5)]
    episodes = [{"episode_id":f"e{i}","conversation_id":"c0001","start":1700000000+i,
                 "end":1700000001+i,"message_ids":[i,i+1],"boundary_reasons":["start"]} for i in (1,3)]
    examples = [{"example_id":f"s{i}","batch_id":"synthetic","episode_id":f"e{i}","conversation_id":"c0001",
                 "scene":"synthetic","split":"train" if i==1 else "test","context_message_ids":[i],"target_message_ids":[i+1],
                 "messages":[{"role":"user","content":"共同问题","source_ids":[i]}],"target":"共同回答",
                 "quality_labels":["adjacent_candidate"]} for i in (1,3)]
    for name,rows in (("normalized_messages",messages),("episodes",episodes),("train",examples[:1]),("dev",[]),("test",examples[1:])):
        (path/(name+".jsonl")).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding="utf-8")
    (path/"message_reviews.json").write_text(json.dumps({str(i):"approved" for i in range(1,5)}),encoding="utf-8")
    (path/"references.json").write_text('{}',encoding="utf-8")
    return path


@pytest.fixture
def imported(tmp_path):
    data = bundle(tmp_path/"data")
    repo = Repository(tmp_path/"app.sqlite3")
    repo.import_t3(data)
    yield repo,data
    repo.close()


def test_migration_foreign_keys_restart_and_batch_identity(imported):
    repo,data=imported
    assert repo.conn.execute('PRAGMA user_version').fetchone()[0]==4
    assert repo.conn.execute('PRAGMA foreign_keys').fetchone()[0]==1
    assert repo.conn.execute('PRAGMA foreign_key_check').fetchall()==[]
    with pytest.raises(sqlite3.IntegrityError),repo.conn:
        repo.conn.execute('INSERT INTO fact_sources VALUES(999,999)')
    before=repo.counts()
    revision=repo.revision('style')
    result=repo.import_t3(data)
    assert result['counts']==before
    assert all(v==0 for v in result['added'].values())
    assert repo.revision('style')==revision
    repo.import_t3(data,batch_id='another-import')
    assert repo.counts()['message_refs']==8
    reopened=Repository(repo.path)
    assert reopened.counts()['style_examples']==4
    reopened.close()


def test_failed_import_rolls_back_entire_batch(tmp_path):
    data=bundle(tmp_path/'data')
    row=json.loads((data/'test.jsonl').read_text(encoding='utf-8'))
    row['context_message_ids']=[999]
    (data/'test.jsonl').write_text(json.dumps(row),encoding='utf-8')
    repo=Repository(tmp_path/'app.sqlite3')
    try:
        with pytest.raises(KeyError):
            repo.import_t3(data)
        assert all(v==0 for v in repo.counts().values())
        assert repo.revision('style')==0
    finally:
        repo.close()


def test_sources_and_separate_typed_queries(imported):
    repo,_=imported
    fact=repo.add_fact('self','synthetic','共同事实',[1])
    trait=repo.add_trait('共同倾向','synthetic','direct',[1])
    repo.add_trait('共同推断','synthetic','inferred',[1])
    turn=repo.save_history('s','anotherme','共同问题','共同实时回复')
    repo.add_feedback(turn,'fact_wrong','共同纠正')
    repo.record_skill(turn,'calculator','expression','ok')
    assert repo.query_facts('共同')[0].kind=='fact'
    assert repo.query_styles('共同')[0].kind=='style'
    assert repo.query_history('s','共同')[0].kind=='history'
    assert len(repo.query_styles('共同'))==1  # train only
    assert repo.query_history('other','共同')==[]
    assert len(repo.query_traits('共同'))==1  # explicit evidence only by default
    for kind,id in (('fact',fact),('trait',trait),('style',1)):
        source=repo.sources(kind,id)[0]
        assert source.batch_id=='synthetic' and source.source_id==1
        assert source.content=='共同问题' and source.episode_id==1
    assert len(repo.query_facts('共同'))==1  # no style/history auto-created facts


def test_fact_correction_refreshes_preexisting_index(imported):
    repo,_=imported
    id=repo.add_fact('self','preference','蓝莓',[1],valid_from=123)
    index=StoredIndex(repo,'fact')
    assert index.search('蓝莓')
    new=repo.correct_fact(id,'草莓',[3])
    assert repo.conn.execute('SELECT status,superseded_by FROM facts WHERE id=?',(id,)).fetchone()[:]==('superseded',new)
    assert repo.query_facts('蓝莓')==[]
    # Character retrieval may match the shared word 莓; only current content may return.
    assert all(hit['id']==new and hit['text']=='草莓' for hit in index.search('蓝莓'))
    assert index.search('草莓')[0]['id']==new
    assert repo.query_facts('草莓')[0].valid_from==123


def test_delete_invalidates_all_derivatives_and_old_cross_connection_indexes(imported):
    repo,data=imported
    repo.add_fact('self','synthetic','共同事实',[1])
    repo.add_trait('共同倾向','synthetic','direct',[1])
    repo.add_document('synthetic.md','共同知识','memory',[1])
    indexes=[StoredIndex(repo,k) for k in ('fact','trait','style','document')]
    assert all(index.search('共同') for index in indexes)
    other=Repository(repo.path)
    try:
        assert other.delete_source(1)
        assert all(index.search('共同')==[] for index in indexes)
        assert repo.query_facts('共同')==[] and repo.query_traits('共同')==[]
        assert repo.counts()['style_examples']==1  # unrelated test sample retained
        assert repo.counts()['chunks']==0
        assert repo.conn.execute('PRAGMA foreign_key_check').fetchall()==[]
        repo.import_t3(data)
        assert repo.counts()['message_refs']==3
        assert repo.query_styles('共同')==[]  # deleted evidence is not resurrected on reimport
    finally:
        other.close()


def test_delete_document_refreshes_index(imported):
    repo,_=imported
    id=repo.add_document('standalone.md','共同知识')
    index=StoredIndex(repo,'document')
    assert index.search('共同')
    repo.delete_document(id)
    assert index.search('共同')==[]
    assert repo.counts()['embedding_items']==0


def test_role_tampering_rolls_back(imported):
    repo,data=imported
    before=repo.counts()
    record=json.loads((data/'test.jsonl').read_text(encoding='utf-8'))
    record['messages'][0]['role']='assistant'
    (data/'test.jsonl').write_text(json.dumps(record),encoding='utf-8')
    with pytest.raises(ValueError,match='role differs'):
        repo.import_t3(data,batch_id='bad')
    assert repo.counts()==before
