"""Local FAISS index whose results are resolved through the current database."""
import faiss
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


class StoredIndex:
    def __init__(self, repository, kind):
        self.repository = repository
        self.kind = kind
        self.revision = -1
        self.ids = []
        self.index = None

    def refresh(self):
        rows = self.repository.index_items(self.kind)
        revision = self.repository.revision(self.kind)
        vectorizer = TfidfVectorizer(analyzer="char",ngram_range=(1,3),max_features=4096)
        index = None
        if rows:
            matrix = np.ascontiguousarray(vectorizer.fit_transform([r["text"] for r in rows]).toarray(),dtype="float32")
            faiss.normalize_L2(matrix)
            index = faiss.IndexFlatIP(matrix.shape[1])
            index.add(matrix)
        with self.repository.conn:
            self.repository.conn.execute("DELETE FROM embedding_items WHERE index_kind=?",(self.kind,))
            column = {"fact":"fact_id","trait":"trait_id","style":"style_id","document":"chunk_id"}[self.kind]
            for vector_id,row in enumerate(rows):
                self.repository.conn.execute(f"INSERT INTO embedding_items(index_kind,{column},vector_id,model,dimension,revision) VALUES(?,?,?,?,?,?)",
                    (self.kind,row["id"],vector_id,"local-char-tfidf",index.d,revision))
        self.ids,self.index,self.vectorizer,self.revision = [r["id"] for r in rows],index,vectorizer,revision

    def search(self, query, limit=3, threshold=.15):
        if limit <= 0:
            raise ValueError("limit must be positive")
        if self.revision != self.repository.revision(self.kind):
            self.refresh()
        if self.index is None:
            return []
        vector = np.ascontiguousarray(self.vectorizer.transform([query]).toarray(),dtype="float32")
        faiss.normalize_L2(vector)
        scores,positions = self.index.search(vector,min(max(limit,8),len(self.ids)))
        # Do not return text cached before a deletion or correction, even across connections.
        current = {r["id"]:r["text"] for r in self.repository.index_items(self.kind)}
        result = []
        for score,position in zip(scores[0],positions[0]):
            if position < 0 or score < threshold:
                continue
            id = self.ids[position]
            if id in current:
                result.append({"kind":self.kind,"id":id,"text":current[id],"score":float(score)})
            if len(result)==limit:
                break
        return result
