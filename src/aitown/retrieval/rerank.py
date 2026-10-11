from pathlib import Path

import numpy as np


class LocalReranker:
    """BGE cross encoder: scores query/passage pairs, not vector similarity."""
    def __init__(self, path, batch_size=8):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        if not Path(path).is_dir():
            raise ValueError("Local reranker directory does not exist")
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=False, use_safetensors=True).eval()
        self.batch_size = batch_size
        self.name = "BAAI/bge-reranker-base"

    def score(self, query, passages):
        scores = []
        for start in range(0, len(passages), self.batch_size):
            pairs = [[query, text] for text in passages[start:start + self.batch_size]]
            encoded = self.tokenizer(pairs, padding=True, truncation=True, max_length=512, return_tensors="pt")
            with self.torch.inference_mode():
                logits = self.model(**encoded).logits.view(-1).float()
                scores.extend(self.torch.sigmoid(logits).cpu().tolist())
        if len(scores) != len(passages) or not np.isfinite(scores).all():
            raise ValueError("Invalid reranker scores")
        return scores


def rank(query, hits, provider):
    scores = provider.score(query, [hit.item.text for hit in hits])
    if len(scores) != len(hits) or not np.isfinite(scores).all():
        raise ValueError("Invalid reranker scores")
    return sorted([hit.model_copy(update={"rerank_score": float(score)}) for hit, score in zip(hits, scores)],
                  key=lambda hit: -hit.rerank_score)
