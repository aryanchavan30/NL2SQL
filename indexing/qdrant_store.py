from __future__ import annotations

import logging
from typing import Callable, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from indexing.store import Document

logger = logging.getLogger("nl2sql")

COLLECTION_NAMES = [
    "db_schema",
    "table_descriptions",
    "view_questions",
    "sql_pairs",
    "instructions",
    "project_meta",
]


class QdrantStore:
    """Drop-in replacement for FAISSStore backed by a Qdrant collection.

    Same public interface: add_documents, search_by_embedding,
    search_by_filter, delete_documents, count_documents, save, load.
    """

    def __init__(self, name: str, dimension: int, url: str, api_key: str = ""):
        self.name = name
        self.dimension = dimension
        self._client = QdrantClient(
            url=url,
            api_key=api_key or None,
            prefer_grpc=False,
        )
        self._ensure_collection()

    def _ensure_collection(self):
        existing = {c.name for c in self._client.get_collections().collections}
        if self.name not in existing:
            self._client.create_collection(
                collection_name=self.name,
                vectors_config=VectorParams(size=self.dimension, distance=Distance.COSINE),
            )

    # ── Write ────────────────────────────────────────────────────────────

    def add_documents(self, documents: list[Document]):
        if not documents:
            return
        zero = [0.0] * self.dimension
        points = [
            PointStruct(
                id=doc.id,
                vector=doc.embedding if doc.embedding is not None else zero,
                payload={
                    "content": doc.content,
                    "meta": doc.meta,
                    "has_embedding": doc.embedding is not None,
                },
            )
            for doc in documents
        ]
        self._client.upsert(collection_name=self.name, points=points, wait=True)

    def delete_documents(self, filter_fn: Callable[[Document], bool]):
        all_docs = self._scroll_all()
        ids_to_delete = [doc.id for doc in all_docs if filter_fn(doc)]
        if ids_to_delete:
            self._client.delete(
                collection_name=self.name,
                points_selector=PointIdsList(ids=ids_to_delete),
                wait=True,
            )

    # ── Read ─────────────────────────────────────────────────────────────

    def search_by_embedding(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        filter_fn: Optional[Callable[[Document], bool]] = None,
        over_fetch_factor: int = 5,
    ) -> list[Document]:
        fetch_k = top_k * over_fetch_factor
        response = self._client.query_points(
            collection_name=self.name,
            query=query_embedding,
            limit=fetch_k,
            with_payload=True,
            query_filter=Filter(
                must=[FieldCondition(key="has_embedding", match=MatchValue(value=True))]
            ),
        )
        docs = []
        for r in response.points:
            doc = Document(
                id=str(r.id),
                content=r.payload["content"],
                meta=r.payload["meta"],
                score=float(r.score),  # Qdrant COSINE already [0, 1]
            )
            if filter_fn is None or filter_fn(doc):
                docs.append(doc)
        docs.sort(key=lambda d: d.score, reverse=True)
        return docs[:top_k]

    def search_by_filter(self, filter_fn: Callable[[Document], bool]) -> list[Document]:
        return [
            Document(id=doc.id, content=doc.content, meta=doc.meta, score=1.0)
            for doc in self._scroll_all()
            if filter_fn(doc)
        ]

    def count_documents(
        self, filter_fn: Optional[Callable[[Document], bool]] = None
    ) -> int:
        if filter_fn is None:
            return self._client.count(collection_name=self.name, exact=True).count
        return sum(1 for doc in self._scroll_all() if filter_fn(doc))

    # ── Persistence (no-op — Qdrant persists server-side) ────────────────

    def save(self):
        pass

    def load(self) -> bool:
        self._ensure_collection()
        return True

    # ── Internal ─────────────────────────────────────────────────────────

    def _scroll_all(self) -> list[Document]:
        docs: list[Document] = []
        offset = None
        while True:
            points, next_offset = self._client.scroll(
                collection_name=self.name,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                docs.append(Document(
                    id=str(p.id),
                    content=p.payload["content"],
                    meta=p.payload["meta"],
                    score=1.0,
                ))
            if next_offset is None:
                break
            offset = next_offset
        return docs


class QdrantStoreManager:
    """Drop-in replacement for FAISSStoreManager backed by Qdrant."""

    def __init__(self, dimension: int, url: str, api_key: str = ""):
        self.dimension = dimension
        self._stores: dict[str, QdrantStore] = {
            name: QdrantStore(name, dimension, url, api_key)
            for name in COLLECTION_NAMES
        }

    def get_store(self, name: str = "db_schema") -> QdrantStore:
        if name not in self._stores:
            raise KeyError(f"Unknown store: {name!r}")
        return self._stores[name]

    def load_all(self):
        pass  # Collections created in __init__; Qdrant already has data

    def save_all(self):
        pass  # Qdrant persists server-side automatically
