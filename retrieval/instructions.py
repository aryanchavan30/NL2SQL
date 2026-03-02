import logging
from typing import Any, Optional

from langchain_core.embeddings import Embeddings

from indexing.store import FAISSStoreManager
from utils.helpers import score_filter

logger = logging.getLogger("nl2sql")


class InstructionsRetrieval:
    """Retrieves relevant user instructions from instructions store."""

    def __init__(
        self,
        store_manager: FAISSStoreManager,
        embeddings: Embeddings,
        similarity_threshold: float = 0.7,
        max_size: int = 10,
    ):
        self._store = store_manager.get_store("instructions")
        self._embeddings = embeddings
        self._threshold = similarity_threshold
        self._max_size = max_size

    async def run(
        self,
        query: str,
        project_id: Optional[str] = None,
        scope: str = "sql",
    ) -> list[dict[str, Any]]:
        filter_fn = None
        if project_id:
            filter_fn = lambda d: (
                d.meta.get("project_id") == project_id
                and d.meta.get("scope", "sql") == scope
            )

        if self._store.count_documents(filter_fn) == 0:
            return []

        query_embedding = await self._embeddings.aembed_query(query)

        documents = self._store.search_by_embedding(
            query_embedding=query_embedding,
            top_k=self._max_size * 2,
            filter_fn=filter_fn,
        )

        filtered = score_filter(documents, self._threshold, self._max_size)

        return [
            {
                "instruction": doc.content,
                "question": doc.meta.get("question", ""),
            }
            for doc in filtered
        ]
