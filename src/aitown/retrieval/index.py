"""FAISS generations and ordinary SQL mappings, with incremental vector reuse."""
import json
import os
import sqlite3
from pathlib import Path

import faiss
import numpy as np

from .models import Item


class PersistentIndex:
    def __init__(self, repository, directory, purpose, provider):
        self.repo = repository
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.purpose, self.provider = purpose, provider
        self.conn = sqlite3.connect(self.directory / "catalog.sqlite3", timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS owner(path TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS generations(
              id INTEGER PRIMARY KEY, purpose TEXT NOT NULL, model TEXT NOT NULL, dimension INTEGER NOT NULL,
              revisions TEXT NOT NULL, provider_state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mappings(
              generation INTEGER REFERENCES generations(id) ON DELETE CASCADE,
              position INTEGER, payload TEXT NOT NULL, PRIMARY KEY(generation,position));
            CREATE TABLE IF NOT EXISTS active(purpose TEXT PRIMARY KEY, generation INTEGER NOT NULL);
        """)
        owner = str(repository.path.resolve())
        old = self.conn.execute("SELECT path FROM owner").fetchone()
        if old and old[0] != owner:
            raise ValueError("Index directory belongs to another application database")
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO owner VALUES(?)", (owner,))
        self.generation = None
        self.index = None
        self.records = []

    def revisions(self):
        return json.dumps(dict(self.repo.conn.execute("SELECT kind,revision FROM data_revisions ORDER BY kind")))

    def path(self, generation):
        return self.directory / f"generation-{generation}.faiss"

    def load(self):
        row = self.conn.execute("SELECT g.* FROM active a JOIN generations g ON g.id=a.generation WHERE a.purpose=?", (self.purpose,)).fetchone()
        if row is None or row["model"] != self.provider.name:
            return False
        if self.provider.reusable and row["dimension"] != self.provider.dimension:
            return False
        if self.generation == row["id"]:
            return True
        index = faiss.read_index(str(self.path(row["id"])))
        records = [Item.model_validate_json(r[0]) for r in self.conn.execute(
            "SELECT payload FROM mappings WHERE generation=? ORDER BY position", (row["id"],))]
        if index.d != row["dimension"] or index.ntotal != len(records):
            raise ValueError("FAISS dimension or mapping count mismatch")
        if not self.provider.reusable:
            self.provider.restore(json.loads(row["provider_state"]))
        self.generation, self.index, self.records = row["id"], index, records
        return True

    def refresh(self, records):
        loaded = self.load()
        if loaded and records == self.records:
            return {"items": len(records), "encoded": 0, "reused": len(records), "loaded": True}
        old = {item.key: (i, item) for i, item in enumerate(self.records)} if loaded else {}
        reusable = self.provider.reusable
        vectors = [None] * len(records)
        changed = []
        for i, item in enumerate(records):
            previous = old.get(item.key)
            if reusable and previous and previous[1].text == item.text:
                vectors[i] = self.index.reconstruct(previous[0])
            else:
                changed.append(i)
        if changed:
            encoded = np.asarray(self.provider.documents([records[i].text for i in changed]), dtype="float32")
            if encoded.ndim != 2 or encoded.shape != (len(changed), self.provider.dimension) or not np.isfinite(encoded).all():
                raise ValueError("Invalid document embeddings")
            for position, vector in zip(changed, encoded):
                vectors[position] = vector
        dimension = self.provider.dimension or 1
        matrix = np.ascontiguousarray(vectors, dtype="float32") if records else np.empty((0, dimension), dtype="float32")
        faiss.normalize_L2(matrix)
        index = faiss.IndexFlatIP(dimension)
        index.add(matrix)
        # Build first, then atomically publish the file and SQL association.
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            gid = self.conn.execute("INSERT INTO generations(purpose,model,dimension,revisions,provider_state) VALUES(?,?,?,?,?)",
                                    (self.purpose, self.provider.name, dimension, self.revisions(), json.dumps(self.provider.state()))).lastrowid
            temporary = self.path(gid).with_suffix(".tmp")
            faiss.write_index(index, str(temporary))
            os.replace(temporary, self.path(gid))
            self.conn.executemany("INSERT INTO mappings VALUES(?,?,?)", [(gid, i, item.model_dump_json()) for i, item in enumerate(records)])
            self.conn.execute("INSERT INTO active VALUES(?,?) ON CONFLICT(purpose) DO UPDATE SET generation=excluded.generation", (self.purpose, gid))
            old_ids = [r[0] for r in self.conn.execute("SELECT id FROM generations WHERE purpose=? AND id<>?", (self.purpose, gid))]
            for old_id in old_ids:
                self.conn.execute("DELETE FROM mappings WHERE generation=?", (old_id,))
                self.conn.execute("DELETE FROM generations WHERE id=?", (old_id,))
        self.generation, self.index, self.records = gid, index, records
        for old_id in old_ids:
            self.path(old_id).unlink(missing_ok=True)
        return {"items": len(records), "encoded": len(changed), "reused": len(records) - len(changed), "loaded": False}

    def coarse(self, query, allowed, limit, threshold):
        if not allowed:
            return []
        vector = np.ascontiguousarray(self.provider.query(query), dtype="float32")
        if vector.shape != (1, self.index.d) or not np.isfinite(vector).all():
            raise ValueError("Query embedding dimension mismatch")
        faiss.normalize_L2(vector)
        # Filtering happens before Top-N; disallowed records cannot consume N.
        positions = np.array(allowed, dtype="int64")
        subset = faiss.IndexFlatIP(self.index.d)
        subset.add(np.stack([self.index.reconstruct(int(i)) for i in positions]))
        scores, ids = subset.search(vector, min(limit, len(allowed)))
        return [(int(positions[i]), float(score)) for i, score in zip(ids[0], scores[0]) if i >= 0 and score >= threshold]

    def close(self):
        self.conn.close()
