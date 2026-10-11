"""Providers never download models or send text over the network at inference."""
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


class TfidfProvider:
    name = "local-char-tfidf-v1"
    reusable = False
    dimension = 0

    def __init__(self):
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), max_features=4096)

    def documents(self, texts):
        matrix = self.vectorizer.fit_transform(texts).toarray().astype("float32")
        self.dimension = matrix.shape[1]
        return matrix

    def query(self, text):
        return self.vectorizer.transform([text]).toarray().astype("float32")

    def state(self):
        if not hasattr(self.vectorizer, "vocabulary_"):
            return {}
        return {"vocabulary": {word: int(index) for word, index in self.vectorizer.vocabulary_.items()},
                "idf": self.vectorizer.idf_.tolist()}

    def restore(self, state):
        if not state:
            return
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), vocabulary=state["vocabulary"])
        self.vectorizer.idf_ = np.array(state["idf"])
        self.dimension = len(state["idf"])


class LocalEmbedding:
    reusable = True

    def __init__(self, path, name="BAAI/bge-small-zh-v1.5", batch_size=16, threads=4):
        import torch
        from transformers import AutoModel, AutoTokenizer
        if not Path(path).is_dir():
            raise ValueError("Local embedding model directory does not exist")
        torch.set_num_threads(threads)
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
        self.model = AutoModel.from_pretrained(str(path), local_files_only=True, trust_remote_code=False,
                                             use_safetensors=True).eval()
        self.name = name
        self.dimension = self.model.config.hidden_size
        self.batch_size = batch_size

    def documents(self, texts):
        chunks = []
        for start in range(0, len(texts), self.batch_size):
            encoded = self.tokenizer(texts[start:start + self.batch_size], padding=True, truncation=True,
                                     max_length=512, return_tensors="pt")
            with self.torch.inference_mode():
                vectors = self.model(**encoded).last_hidden_state[:, 0]
            chunks.append(vectors.cpu().numpy())
        return np.concatenate(chunks).astype("float32") if chunks else np.empty((0, self.dimension), dtype="float32")

    def query(self, text):
        return self.documents(["为这个句子生成表示以用于检索相关文章：" + text])

    def state(self):
        return {}
