"""Question-only FAISS IDs; embeddings are prepared outside read-only skills."""
import json
from pathlib import Path

import faiss
import numpy as np

from .repository import BankRepository


def index_directory(config, course=None):
    if course is None:
        return config.root/'indexes'
    with BankRepository(config.database,readonly=True) as repo:
        cid=repo.course_id(course)
        row=repo.conn.execute('SELECT rowid FROM courses WHERE id=?',(cid,)).fetchone()
        if row is None:
            raise ValueError('课程不存在')
        return config.root/'indexes'/('course-'+str(row[0]))


def build_index(config, provider=None, *, course=None):
    from ..retrieval.providers import LocalEmbedding
    provider = provider or LocalEmbedding(config.embedding_dir)
    with BankRepository(config.database, readonly=True) as repo:
        courses = [repo.course_id(course)] if course else [r[0] for r in repo.conn.execute('SELECT id FROM courses')]
        questions = [q for course in courses for q in repo.rows(course)]
    vectors = provider.documents([q['text']+' '+' '.join(q['topics']) for q in questions])
    if not len(questions):
        raise ValueError('题库没有可建立向量索引的题目')
    faiss.normalize_L2(vectors)
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(vectors.shape[1]))
    index.add_with_ids(vectors, np.asarray([q['id'] for q in questions], dtype='int64'))
    directory = index_directory(config,course)
    directory.mkdir(parents=True,exist_ok=True)
    target = directory/'questions.faiss'
    temporary = target.with_suffix('.tmp')
    faiss.write_index(index,str(temporary))
    temporary.replace(target)
    (directory/'model.json').write_text(json.dumps({'provider':provider.name,'dimension':vectors.shape[1],
        'model_directory':str(config.embedding_dir.resolve()),'count':len(questions)},ensure_ascii=False),encoding='utf-8')
    return {'questions':len(questions),'dimension':vectors.shape[1],'bytes':target.stat().st_size}


class SemanticSearch:
    def __init__(self, config):
        self.config=config
        self.embedding=None
        self.reranker=None

    def scores(self, query, candidates):
        from ..retrieval.providers import LocalEmbedding
        courses={q['course_id'] for q in candidates}
        target=index_directory(self.config,next(iter(courses)))/'questions.faiss' if len(courses)==1 else self.config.root/'indexes'/'questions.faiss'
        if not target.is_file():
            target=self.config.root/'indexes'/'questions.faiss'
        if not target.is_file():
            raise ValueError('题库独立向量索引尚未准备，请运行 index')
        meta=json.loads((target.parent/'model.json').read_text(encoding='utf-8'))
        if Path(meta['model_directory']).resolve()!=self.config.embedding_dir.resolve():
            raise ValueError('题库索引使用了不同的向量模型，请重建索引')
        self.embedding=self.embedding or LocalEmbedding(self.config.embedding_dir)
        index=faiss.read_index(str(target))
        if index.d!=self.embedding.dimension or index.d!=meta['dimension']:
            raise ValueError('题库索引维度与当前模型不一致')
        indexed=set(map(int,faiss.vector_to_array(index.id_map)))
        if not {q['id'] for q in candidates}<=indexed:
            raise ValueError('题库已更新，向量索引待刷新')
        vector=self.embedding.query(query)
        faiss.normalize_L2(vector)
        scores,ids=index.search(vector,index.ntotal)
        permitted={q['id'] for q in candidates}
        return {int(qid):float(score) for qid,score in zip(ids[0],scores[0]) if int(qid) in permitted}

    def rerank(self,query,questions):
        from ..retrieval.rerank import LocalReranker
        self.reranker=self.reranker or LocalReranker(self.config.reranker_dir)
        return self.reranker.score(query,[q['text'] for q in questions])
