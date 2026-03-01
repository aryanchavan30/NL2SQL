import logging
import os
import pickle
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import faiss
import numpy as np

logger = logging.getLogger("nl2sql")


@dataclass
class Document:
    content: str
    meta: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    embedding: Optional[list[float]] = None
    score: float = 0.0


class FAISSStore:
    """Single named FAISS index with metadata filtering.

    Supports two kinds of documents:
      - **Embedded documents** — stored in the FAISS index *and* ``_documents``
        list (position-synced). Searchable by both embedding and filter.
      - **Metadata-only documents** — stored in ``_meta_only_documents``.
        Searchable by filter only (used by project_meta store).
    """

    def __init__(self, name: str, dimension: int, persist_dir: str = "./faiss_indices"):
        self.name = name
        self.dimension = dimension
        self.persist_dir = persist_dir
        self._index: Optional[faiss.IndexFlatIP] = None
        self._documents: list[Document] = []
        self._meta_only_documents: list[Document] = []

    def _ensure_index(self):
        if self._index is None:
            self._index = faiss.IndexFlatIP(self.dimension)

    def add_documents(self, documents: list[Document]):
        if not documents:
            return

        with_emb = [d for d in documents if d.embedding is not None]
        without_emb = [d for d in documents if d.embedding is None]

        if with_emb:
            self._ensure_index()
            embeddings = [d.embedding for d in with_emb]
            vectors = np.array(embeddings, dtype=np.float32)
            faiss.normalize_L2(vectors)
            self._index.add(vectors)
            self._documents.extend(with_emb)

        if without_emb:
            self._meta_only_documents.extend(without_emb)

    def search_by_embedding(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        filter_fn: Optional[Callable[[Document], bool]] = None,
        over_fetch_factor: int = 5,
    ) -> list[Document]:
        if self._index is None or self._index.ntotal == 0:
            return []

        fetch_k = min(top_k * over_fetch_factor, self._index.ntotal)

        query_vec = np.array([query_embedding], dtype=np.float32)
        faiss.normalize_L2(query_vec)

        scores, indices = self._index.search(query_vec, fetch_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._documents):
                continue
            doc = self._documents[idx]
            normalized_score = (float(score) + 1) / 2  # [-1,1] -> [0,1]
            candidate = Document(
                id=doc.id,
                content=doc.content,
                meta=doc.meta,
                embedding=doc.embedding,
                score=normalized_score,
            )
            if filter_fn is None or filter_fn(candidate):
                results.append(candidate)

        results.sort(key=lambda d: d.score, reverse=True)
        return results[:top_k]

    def search_by_filter(
        self, filter_fn: Callable[[Document], bool]
    ) -> list[Document]:
        all_docs = self._documents + self._meta_only_documents
        return [
            Document(
                id=doc.id,
                content=doc.content,
                meta=doc.meta,
                embedding=doc.embedding,
                score=1.0,
            )
            for doc in all_docs
            if filter_fn(doc)
        ]

    def delete_documents(
        self, filter_fn: Callable[[Document], bool]
    ):
        # Rebuild embedded documents + FAISS index
        remaining = [doc for doc in self._documents if not filter_fn(doc)]
        self._documents = []
        self._index = None

        if remaining:
            embeddings = [doc.embedding for doc in remaining]
            self._ensure_index()
            vectors = np.array(embeddings, dtype=np.float32)
            faiss.normalize_L2(vectors)
            self._index.add(vectors)
            self._documents = remaining

        # Also filter metadata-only documents
        self._meta_only_documents = [
            doc for doc in self._meta_only_documents if not filter_fn(doc)
        ]

    def count_documents(
        self, filter_fn: Optional[Callable[[Document], bool]] = None
    ) -> int:
        all_docs = self._documents + self._meta_only_documents
        if filter_fn is None:
            return len(all_docs)
        return sum(1 for doc in all_docs if filter_fn(doc))

    def save(self):
        os.makedirs(self.persist_dir, exist_ok=True)
        if self._index is not None:
            faiss.write_index(
                self._index,
                os.path.join(self.persist_dir, f"{self.name}.faiss"),
            )
        meta_path = os.path.join(self.persist_dir, f"{self.name}.meta.pkl")
        with open(meta_path, "wb") as f:
            pickle.dump(
                {"documents": self._documents, "meta_only": self._meta_only_documents},
                f,
            )

    def load(self) -> bool:
        index_path = os.path.join(self.persist_dir, f"{self.name}.faiss")
        meta_path = os.path.join(self.persist_dir, f"{self.name}.meta.pkl")

        if not os.path.exists(meta_path):
            return False

        try:
            with open(meta_path, "rb") as f:
                data = pickle.load(f)

            # Support both old format (plain list) and new format (dict)
            if isinstance(data, dict):
                self._documents = data.get("documents", [])
                self._meta_only_documents = data.get("meta_only", [])
            else:
                # Backward compat: old format was just a list of documents
                self._documents = data
                self._meta_only_documents = []

            if os.path.exists(index_path):
                self._index = faiss.read_index(index_path)
            else:
                self._ensure_index()

            total = len(self._documents) + len(self._meta_only_documents)
            logger.info(
                f"Loaded FAISS store '{self.name}' with {total} documents"
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to load FAISS store '{self.name}': {e}")
            return False


COLLECTION_NAMES = [
    "db_schema",
    "table_descriptions",
    "view_questions",
    "sql_pairs",
    "instructions",
    "project_meta",
]


class FAISSStoreManager:
    """Manages multiple named FAISS stores."""

    def __init__(self, dimension: int, persist_dir: str = "./faiss_indices"):
        self.dimension = dimension
        self.persist_dir = persist_dir
        self._stores: dict[str, FAISSStore] = {}

        for name in COLLECTION_NAMES:
            self._stores[name] = FAISSStore(name, dimension, persist_dir)

    def get_store(self, name: str = "db_schema") -> FAISSStore:
        if name not in self._stores:
            self._stores[name] = FAISSStore(name, self.dimension, self.persist_dir)
        return self._stores[name]

    def load_all(self):
        for store in self._stores.values():
            store.load()

    def save_all(self):
        for store in self._stores.values():
            store.save()
