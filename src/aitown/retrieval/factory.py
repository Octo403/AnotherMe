from pathlib import Path

from .providers import LocalEmbedding
from .rerank import LocalReranker
from .search import Retriever


def create_retriever(repository, directory, embedding_path=None, reranker_path=None, *, top_n=20, top_k=4,
                     threshold=.45, rerank_threshold=.01):
    provider = LocalEmbedding(embedding_path) if embedding_path else None
    reranker = LocalReranker(reranker_path) if reranker_path else None
    return Retriever(repository, Path(directory), provider, reranker, top_n=top_n, top_k=top_k,
                     threshold=threshold if provider else .15, rerank_threshold=rerank_threshold)
