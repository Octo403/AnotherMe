import time
from pathlib import Path

from .corpus import items
from .index import PersistentIndex
from .models import Hit, SearchFilter, SearchResult
from .providers import TfidfProvider
from .rerank import rank


class Retriever:
    def __init__(self, repository, directory, provider=None, reranker=None, *, top_n=20, top_k=4,
                 threshold=.15, rerank_threshold=.01, allow_fallback=True):
        if not 1 <= top_k <= top_n:
            raise ValueError("Require 1 <= top_k <= top_n")
        self.repo, self.directory = repository, Path(directory)
        self.provider, self.reranker = provider, reranker
        self.top_n, self.top_k = top_n, top_k
        self.threshold, self.rerank_threshold = threshold, rerank_threshold
        self.allow_fallback = allow_fallback
        self.indexes = {}

    def index(self, purpose, fallback=False):
        key = (purpose, fallback)
        if key not in self.indexes:
            provider = TfidfProvider() if fallback or self.provider is None else self.provider
            # TF-IDF vocabularies are per-purpose; semantic models are shared.
            directory = self.directory / ("tfidf" if fallback or self.provider is None else "semantic")
            self.indexes[key] = PersistentIndex(self.repo, directory, purpose, provider)
        return self.indexes[key]

    def current(self, purpose):
        with self.repo.conn:
            self.repo.conn.execute("BEGIN")
            return items(self.repo, purpose)

    def refresh(self, purpose=None):
        return {p: self.index(p).refresh(self.current(p)) for p in ([purpose] if purpose else ["general", "personal", "style"])}

    def allowed(self, item, filters):
        now = int(time.time()) if filters.as_of is None else filters.as_of
        if filters.subject is not None and item.subject != filters.subject:
            return False
        if filters.scope and filters.scope not in item.scopes:
            return False
        if filters.scene and filters.scene != item.scene and filters.scene not in item.response_scenes:
            return False
        if filters.category is not None and item.category != filters.category:
            return False
        if filters.time_from is not None and (item.timestamp is None or item.timestamp < filters.time_from):
            return False
        if filters.time_to is not None and (item.timestamp is None or item.timestamp > filters.time_to):
            return False
        if filters.purpose != "personal":
            return True
        if item.recorded_at is not None and item.recorded_at > now:
            return False
        if item.valid_from is not None and item.valid_from > now:
            return False
        if item.valid_to is not None and item.valid_to <= now:
            return False
        if item.status == "superseded" and (filters.as_of is None or item.valid_to is None):
            return False
        if item.category == "plan" and item.valid_to is None:
            return False
        # Self claims must not be derived from another person's statement.
        if item.subject == "self" and any(s.direction != "outgoing" or s.speaker_id != "self" for s in item.sources):
            return False
        return True

    def _coarse(self, query, filters, fallback=False):
        index = self.index(filters.purpose, fallback)
        records = self.current(filters.purpose)
        index.refresh(records)
        allowed = [i for i, item in enumerate(records) if self.allowed(item, filters)]
        hits = [Hit(item=records[i], score=score) for i, score in index.coarse(query, allowed, self.top_n,
                                                                          .15 if fallback else self.threshold)]
        return hits, index.provider.name

    def search(self, query, filters=None, *, rerank=True):
        filters = filters or SearchFilter()
        if not query.strip():
            raise ValueError("Query cannot be empty")
        if filters.purpose in ("personal", "style") and (not filters.scope or (filters.purpose == "personal" and not filters.subject)):
            return SearchResult(status="ambiguous_scope")
        if filters.purpose=='personal' and filters.category=='preference':
            # A scoped request for the preference category is a database listing,
            # not similarity to a guessed beverage. Only direct current records
            # passing the same owner/scope/time checks can support the answer.
            start=time.perf_counter()
            records=[item for item in self.current('personal') if self.allowed(item,filters)
                     and item.basis=='direct' and item.status in ('active','confirmed','superseded')]
            current={item.key:item for item in self.current('personal')}
            records=[item for item in records if current.get(item.key)==item and self.allowed(item,filters)]
            hits=[Hit(item=item,score=1) for item in records[:self.top_k]]
            return SearchResult(status='ok' if hits else 'no_personal_data',hits=hits,coarse=hits,ranked=hits,
                                backend='sqlite-category',timings={'coarse_seconds':time.perf_counter()-start,'rerank_seconds':0})
        warnings, timings = [], {}
        start = time.perf_counter()
        try:
            coarse, backend = self._coarse(query, filters)
        except Exception as exc:
            warnings.append(f"检索故障：{type(exc).__name__}")
            if not self.allow_fallback:
                return SearchResult(status="retrieval_error", warnings=warnings)
            try:
                coarse, backend = self._coarse(query, filters, fallback=True)
                warnings.append("已回退本地 TF-IDF，结果可能缺少语义召回。")
            except Exception as fallback_error:
                return SearchResult(status="retrieval_error", warnings=[*warnings, f"回退失败：{type(fallback_error).__name__}"])
        timings["coarse_seconds"] = time.perf_counter() - start
        ranked = coarse
        start = time.perf_counter()
        if coarse and rerank and self.reranker:
            try:
                ranked = rank(query, coarse, self.reranker)
            except Exception as exc:
                # Personal questions fail closed: an unavailable relevance check
                # cannot turn arbitrary nearest neighbours into personal facts.
                return SearchResult(status="retrieval_error", coarse=coarse, backend=backend,
                                    warnings=[*warnings, f"精排故障：{type(exc).__name__}"], timings=timings)
        timings["rerank_seconds"] = time.perf_counter() - start
        relevant = [h for h in ranked if h.rerank_score is None or h.rerank_score >= self.rerank_threshold]
        # Resolve evidence again after model execution. A concurrent deletion or
        # correction must not leak cached text in selected or debug results.
        current = {item.key: item for item in self.current(filters.purpose)}
        coarse = [h for h in coarse if current.get(h.item.key) == h.item]
        ranked = [h for h in ranked if current.get(h.item.key) == h.item]
        relevant = [h for h in relevant if current.get(h.item.key) == h.item]
        direct, uncertain = [], []
        seen = set()
        for hit in relevant:
            item = hit.item
            duplicate = item.text.strip()
            if duplicate in seen:
                continue
            seen.add(duplicate)
            if filters.purpose == "personal" and (item.basis != "direct" or item.status not in ("active", "confirmed", "superseded")):
                uncertain.append(hit)
            else:
                direct.append(hit)
        status = "ok" if direct else "inference_only" if uncertain else "no_personal_data" if filters.purpose == "personal" else "no_match"
        return SearchResult(status=status, hits=direct[:self.top_k], coarse=coarse, ranked=ranked,
                            warnings=warnings, timings=timings, backend=backend)

    def close(self):
        for index in self.indexes.values():
            index.close()
