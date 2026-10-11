from pathlib import Path

import faiss
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .models import Citation
from .retrieval.chunker import document_chunks


class KnowledgeBase:
    def __init__(self, directory: Path, threshold: float = 0.15, model=None):
        self.directory = directory
        self.threshold = threshold
        self.model = model
        self.chunks: list[tuple[str, int, str]] = []
        self.index = None
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), max_features=4096)
        self.refresh()

    def refresh(self):
        chunks = []
        if self.directory.exists():
            for path in sorted(self.directory.rglob("*")):
                if path.suffix.lower() not in (".txt", ".md") or not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8-sig")
                for number, piece in enumerate(document_chunks(text), 1):
                    if piece:
                        chunks.append((str(path.relative_to(self.directory)), number, piece))
        index = None
        vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), max_features=4096)
        if chunks:
            texts = [c[2] for c in chunks]
            vectors = (self.model.embeddings(texts) if self.model
                       else vectorizer.fit_transform(texts).toarray())
            matrix = np.ascontiguousarray(vectors, dtype="float32")
            faiss.normalize_L2(matrix)
            index = faiss.IndexFlatIP(matrix.shape[1])
            index.add(matrix)
        # Only replace the active corpus after rebuilding succeeds.
        self.chunks, self.index, self.vectorizer = chunks, index, vectorizer

    def search(self, query: str, limit: int = 3) -> list[Citation]:
        if self.index is None:
            return []
        vectors = (self.model.embeddings([query]) if self.model
                   else self.vectorizer.transform([query]).toarray())
        matrix = np.ascontiguousarray(vectors, dtype="float32")
        faiss.normalize_L2(matrix)
        scores, ids = self.index.search(matrix, min(8, len(self.chunks)))
        results = []
        seen = set()
        for score, idx in zip(scores[0], ids[0]):
            if idx < 0 or score < self.threshold:
                continue
            source, chunk, text = self.chunks[idx]
            if text in seen:
                continue
            seen.add(text)
            results.append(Citation(source=source, chunk=chunk, text=text, score=float(score)))
        return results[:limit]

    def current(self, citation):
        root = self.directory.resolve()
        path = (root/citation.source).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return False
        chunks = list(document_chunks(path.read_text(encoding='utf-8-sig')))
        return 1 <= citation.chunk <= len(chunks) and chunks[citation.chunk-1] == citation.text
