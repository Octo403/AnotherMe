import numpy as np
import pytest

from aitown.config import Settings
from aitown.graph import AnotherMeAgent
from aitown.memory import MemoryStore, extract_batch
from aitown.memory.repository import review_record
from aitown.retrieval import Retriever, SearchFilter
from aitown.retrieval.chunker import document_chunks
from aitown.retrieval.corpus import items
from aitown.storage import Repository
from test_memory import message, populated, stamp
from test_storage import bundle


class Embedding:
    name = "test-semantic-v1"
    dimension = 3
    reusable = True

    def __init__(self):
        self.encoded = 0

    def documents(self, texts):
        self.encoded += len(texts)
        return np.array([[1 + int('雨' in t), 1 + int('茶' in t), 1] for t in texts], dtype="float32")

    def query(self, text):
        return self.documents([text])

    def state(self):
        return {}


def test_preference_category_listing_keeps_scope_dates_and_superseded_values(tmp_path):
    from aitown.runtime import RuntimeStore, UserStatement
    repo=Repository(tmp_path/'app.sqlite3')
    memory=RuntimeStore(repo)
    first=memory.record_statement(UserStatement(attribute='preference',value='喜欢咖啡',scope='c0001'),'我喜欢咖啡','s','anotherme')
    second=memory.record_statement(UserStatement(kind='user_correction',attribute='preference',value='喜欢白开水',scope='c0001',corrects_fact_id=first),
                                   '纠正：喜欢白开水','s','anotherme')
    memory.record_statement(UserStatement(attribute='preference',value='秘密饮料',scope='c0002'),'秘密饮料','other','anotherme')
    search=Retriever(repo,tmp_path/'index',threshold=1)
    try:
        result=search.search('我现在喜欢喝什么',SearchFilter(purpose='personal',subject='self',scope='c0001',category='preference'))
        assert result.backend=='sqlite-category' and result.status=='ok'
        assert [hit.item.entity_id for hit in result.hits]==[second]
        assert '秘密' not in result.model_dump_json() and '咖啡' not in result.model_dump_json()
        assert search.search('偏好是什么',SearchFilter(purpose='personal',subject='self',category='preference')).status=='ambiguous_scope'
        assert search.search('偏好是什么',SearchFilter(purpose='personal',subject='self',scope='c0001',category='preference',as_of=1)).status=='no_personal_data'
        read_current=search.current
        changed=[]
        def supersede_between_reads(purpose):
            records=read_current(purpose)
            if not changed:
                other=Repository(repo.path)
                with other.conn:other.conn.execute("UPDATE facts SET status='superseded',valid_to=0 WHERE id=?",(second,))
                other.close();changed.append(True)
            return records
        search.current=supersede_between_reads
        result=search.search('偏好是什么',SearchFilter(purpose='personal',subject='self',scope='c0001',category='preference'))
        assert result.status=='no_personal_data' and result.hits==result.coarse==result.ranked==[]
        assert '白开水' not in result.model_dump_json()
    finally:search.close();memory.close()


def test_chunker_paragraphs_overlap_and_separate_memories(tmp_path):
    pieces = list(document_chunks('第一段\n\n' + '长' * 650 + '\n\n最后一段', size=100, overlap=20))
    assert pieces[0] == '第一段' and pieces[-1] == '最后一段'
    assert all(len(p) <= 100 for p in pieces)
    repo = populated(tmp_path, [message('我昨天去了图书馆'), message('我昨天去了食堂', outgoing=False)], ['train', 'train'])
    try:
        entries = extract_batch(repo)
        MemoryStore(repo).save(entries, {m.entry_key: review_record(m) for m in entries})
        records = [r for r in items(repo, 'personal') if r.subject != 'conversation']
        assert len(records) == 2
        assert len({r.subject for r in records}) == 2
        assert all(len(r.sources) == 1 for r in records)
        assert not any('图书馆' in r.text and '食堂' in r.text for r in records)
    finally:
        repo.close()


def test_restart_incremental_update_delete_and_model_dimension(tmp_path):
    repo = Repository(tmp_path/'app.sqlite3')
    provider = Embedding()
    search = Retriever(repo, tmp_path/'index', provider, threshold=0)
    try:
        a = repo.add_document('a.md', '下雨带伞')
        b = repo.add_document('b.md', '喝茶')
        assert search.refresh('general')['general']['encoded'] == 2
        search.close()
        search = Retriever(repo, tmp_path/'index', provider, threshold=0)
        assert search.refresh('general')['general']['encoded'] == 0
        repo.upsert_document('a.md', '下雨出门带伞')
        result = search.refresh('general')['general']
        assert result['encoded'] == 1 and result['reused'] == 1
        other = Repository(repo.path)
        other.delete_document(a)
        other.close()
        hits = search.search('下雨').hits
        assert [h.item.entity_id for h in hits] == [b]
        assert search.refresh('general')['general']['encoded'] == 0
        provider.name = 'test-semantic-v2'
        assert search.refresh('general')['general']['encoded'] == 1
        provider.dimension = 4
        with pytest.raises(ValueError, match='Invalid document embeddings'):
            search.refresh('general')
    finally:
        search.close()
        repo.close()


def test_tfidf_restart_empty_and_document_purpose_isolation(tmp_path):
    repo = Repository(tmp_path/'app.sqlite3')
    search = Retriever(repo, tmp_path/'index')
    try:
        assert search.search('空库').status == 'no_match'
        repo.add_document('ok.md', '雨天带伞')
        repo.add_document('style.md', '雨天秘密', 'style')
        repo.add_document('memory.md', '雨天秘密', 'reviewed_memory_train')
        assert len(search.search('雨天').hits) == 1
        search.close()
        search = Retriever(repo, tmp_path/'index')
        assert len(search.search('雨天').hits) == 1
    finally:
        search.close()
        repo.close()


def test_failed_rebuild_keeps_previous_active_generation(tmp_path, monkeypatch):
    repo = Repository(tmp_path/'app.sqlite3')
    search = Retriever(repo, tmp_path/'index', Embedding(), allow_fallback=False)
    try:
        repo.add_document('old.md', '下雨')
        search.refresh('general')
        index = search.index('general')
        old_id = index.generation
        repo.add_document('new.md', '茶')
        def fail(*args):
            raise OSError('disk full')
        monkeypatch.setattr('aitown.retrieval.index.faiss.write_index', fail)
        assert search.search('茶').status == 'retrieval_error'
        assert index.conn.execute('SELECT generation FROM active').fetchone()[0] == old_id
        assert index.path(old_id).exists()
    finally:
        search.close()
        repo.close()


def test_personal_filter_before_top_n_subject_scope_and_time(tmp_path):
    old, new = stamp('2025-03-10T10:00:00'), stamp('2025-04-10T10:00:00')
    repo = populated(tmp_path, [message('我喜欢咖啡', timestamp=old), message('我现在喜欢茶', timestamp=new),
                               message('我喜欢茶', outgoing=False, timestamp=old)], ['train']*3)
    entries = extract_batch(repo)
    MemoryStore(repo).save(entries, {m.entry_key: review_record(m) for m in entries})
    search = Retriever(repo, tmp_path/'index', Embedding(), top_n=1, top_k=1, threshold=0)
    try:
        assert search.search('我的偏好', SearchFilter(purpose='personal', subject='self')).status == 'ambiguous_scope'
        historical = search.search('我的偏好', SearchFilter(purpose='personal', subject='self', scope='c0001', as_of=old+1))
        assert historical.status == 'ok' and '咖啡' in historical.hits[0].item.text
        current = search.search('茶', SearchFilter(purpose='personal', subject='self', scope='c0001', as_of=new+1))
        assert current.status == 'ok' and '咖啡' not in current.hits[0].item.text
        assert all(h.item.subject == 'self' for h in current.coarse)
        assert search.search('茶', SearchFilter(purpose='personal', subject='self', scope='wrong')).status == 'no_personal_data'
        assert search.search('茶', SearchFilter(purpose='personal', subject='self', scope='c0001', time_from=new+1)).status == 'no_personal_data'
    finally:
        search.close()
        repo.close()


def test_style_train_inputs_only_and_source_delete(tmp_path):
    repo = Repository(tmp_path/'app.sqlite3')
    repo.import_t3(bundle(tmp_path/'data'))
    search = Retriever(repo, tmp_path/'index')
    try:
        records = items(repo, 'style')
        assert len(records) == 1 and '共同回答' not in records[0].text
        assert search.search('共同问题', SearchFilter(purpose='style', scope='c0001')).status == 'ok'
        repo.delete_source(1)
        assert search.search('共同问题', SearchFilter(purpose='style', scope='c0001')).status == 'no_match'
    finally:
        search.close()
        repo.close()


def test_real_rerank_interface_threshold_order_and_error(tmp_path):
    class Reranker:
        def score(self, query, passages):
            return [.9 if '伞' in p else .001 for p in passages]
    repo = Repository(tmp_path/'app.sqlite3')
    search = Retriever(repo, tmp_path/'index', Embedding(), Reranker(), threshold=0)
    try:
        repo.add_document('rain.md', '下雨带伞')
        repo.add_document('tea.md', '下雨喝茶')
        result = search.search('雨天出门')
        assert len(result.coarse) == 2 and len(result.ranked) == 2 and len(result.hits) == 1
        assert '伞' in result.hits[0].item.text
        search.reranker.score = lambda q, ps: [float('nan')]*len(ps)
        assert search.search('雨天').status == 'retrieval_error'
    finally:
        search.close()
        repo.close()


def test_delete_during_rerank_removes_cached_sources_and_debug_text(tmp_path):
    repo = Repository(tmp_path/'app.sqlite3')
    doc = repo.add_document('rain.md', '下雨带伞')
    class Reranker:
        def score(self, query, passages):
            other = Repository(repo.path)
            other.delete_document(doc)
            other.close()
            return [.9]*len(passages)
    search = Retriever(repo, tmp_path/'index', Embedding(), Reranker(), threshold=0)
    try:
        result = search.search('下雨')
        assert result.status == 'no_match'
        assert result.hits == result.coarse == result.ranked == []
    finally:
        search.close()
        repo.close()


def test_inference_only_never_becomes_evidence(tmp_path):
    from aitown.memory.models import Memory, Evidence
    repo = populated(tmp_path, [message('可能喜欢茶')], ['train'])
    row = dict(repo.conn.execute('SELECT * FROM message_refs').fetchone())
    evidence = Evidence(message_id=1, batch_id='synthetic', source_id=1, timestamp=row['timestamp'],
                        direction='outgoing', speaker_id='self', excerpt=row['content'])
    memory = Memory(entry_key='guess', category='inference', subject='self', basis='inferred', status='candidate',
                    scope='c0001', split='train', recorded_at=row['timestamp'], summary='可能喜欢茶', evidence=[evidence])
    MemoryStore(repo).save([memory])
    search = Retriever(repo, tmp_path/'index', Embedding(), threshold=0)
    try:
        result = search.search('茶', SearchFilter(purpose='personal', subject='self', scope='c0001'))
        assert result.status == 'inference_only' and result.hits == []
    finally:
        search.close()
        repo.close()


def test_graph_unknown_personal_skips_generation_and_resets(tmp_path):
    repo = Repository(tmp_path/'app.sqlite3')
    repo.close()
    settings = Settings(knowledge_dir=tmp_path/'knowledge', memory_db=tmp_path/'chat.sqlite3',
                        retrieval_database=repo.path, retrieval_index_dir=tmp_path/'index')
    agent = AnotherMeAgent(settings)
    class Model:
        def structured(self, messages, schema):
            from aitown.models import Decision
            assert schema is Decision  # no generation call for absent personal evidence
            return Decision(intent='knowledge', query='我的月球旅游经历')
        def close(self):
            pass
    agent.model = Model()
    try:
        reply = agent.ask('我的月球旅游经历是什么', scope='c0001')
        assert reply.retrieval_status == 'no_personal_data' and '我不知道' in reply.answer
        agent.model = None
        assert agent.ask('你好').retrieval_status is None
    finally:
        agent.close()


def test_embedding_fault_uses_tfidf_with_observable_warning(tmp_path):
    class FailedEmbedding(Embedding):
        def documents(self, texts):
            raise RuntimeError('encoder unavailable')
    repo = Repository(tmp_path/'app.sqlite3')
    repo.add_document('rain.md', '雨天出门带雨伞')
    search = Retriever(repo, tmp_path/'index', FailedEmbedding())
    try:
        result = search.search('雨天')
        assert result.status == 'ok' and result.backend == 'local-char-tfidf-v1'
        assert any('回退' in warning for warning in result.warnings)
    finally:
        search.close()
        repo.close()


def test_style_scene_filter_happens_before_coarse_top_n(tmp_path):
    repo = Repository(tmp_path/'app.sqlite3')
    repo.import_t3(bundle(tmp_path/'data'))
    search = Retriever(repo, tmp_path/'index', Embedding(), threshold=0, top_n=1, top_k=1)
    try:
        assert search.search('共同问题', SearchFilter(purpose='style', scope='c0001', scene='chat')).status == 'ok'
        result = search.search('共同问题', SearchFilter(purpose='style', scope='c0001', scene='comfort'))
        assert result.status == 'no_match' and result.coarse == []
    finally:
        search.close()
        repo.close()
