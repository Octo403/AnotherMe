import json

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .models import StyleHit
from .statistics import guidance, request_scene, response_scenes, training_rows


class StyleRetriever:
    """Offline input similarity with scope/scene filtering, not a Rerank model."""
    def __init__(self, repo, semantic=None):
        self.repo = repo
        self.semantic = semantic

    def search(self, query, *, scope, scene=None, limit=3):
        if not scope: return []
        if limit < 1 or limit > 5: raise ValueError("Style limit must be 1..5")
        scene = scene or request_scene(query)
        rows = [r for r in training_rows(self.repo) if r["conversation_id"]==scope]
        if not rows: return []
        if self.semantic:
            from ..retrieval import SearchFilter
            result = self.semantic.search(query, SearchFilter(purpose="style", scope=scope, scene=scene))
            if result.status == "retrieval_error":
                raise RuntimeError("Semantic style retrieval failed")
            by_id = {row['id']: row for row in rows}
            selected = []
            for hit in result.ranked:
                if hit.rerank_score is not None and hit.rerank_score < self.semantic.rerank_threshold:
                    continue
                row = by_id.get(hit.item.entity_id)
                if row is None:
                    continue
                scenes = response_scenes(row['target'])
                if scene not in scenes:
                    continue
                sources = self.repo.sources('style', row['id'])
                if not sources:
                    continue
                selected.append(StyleHit(id=row['id'], scope=scope, scenes=scenes, score=hit.score,
                    context=json.loads(row['context']), target=row['target'],
                    source_ids=[s.message_id for s in sources], guidance=guidance(row['target'])))
                if len(selected) == limit:
                    break
            return selected
        contexts = [json.loads(r["context"]) for r in rows]
        # Historical targets never enter the ranking features or query.
        inputs = ['\n'.join(m['content'] for m in context if m['role']=='user') for context in contexts]
        vectorizer = TfidfVectorizer(analyzer='char',ngram_range=(1,3),max_features=4096)
        matrix = vectorizer.fit_transform(inputs)
        scores = (matrix @ vectorizer.transform([query]).T).toarray().ravel()
        result = []
        for index in np.argsort(-scores,kind='stable'):
            row=rows[int(index)]
            scenes=response_scenes(row['target'])
            if scene not in scenes or scores[index] <= 0: continue
            sources=self.repo.sources('style',row['id'])
            if not sources: continue
            result.append(StyleHit(id=row['id'],scope=scope,scenes=scenes,score=float(scores[index]),
                                   context=contexts[index],target=row['target'],source_ids=[s.message_id for s in sources],guidance=guidance(row['target'])))
            if len(result)==limit:break
        return result
