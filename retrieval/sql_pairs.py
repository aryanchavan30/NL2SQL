import logging
from typing import Any, Optional

from langchain_ollama import OllamaEmbeddings

from indexing.store import FAISSStoreManager
from utils.helpers import score_filter

logger = logging.getLogger("nl2sql")


class SqlPairsRetrieval:
    """Retrieves similar SQL question-answer pairs from sql_pairs store."""

    def __init__(
        self,
        store_manager: FAISSStoreManager,
        embeddings: OllamaEmbeddings,
        similarity_threshold: float = 0.7,
        max_size: int = 10,
    ):
        self._store = store_manager.get_store("sql_pairs")
        self._embeddings = embeddings
        self._threshold = similarity_threshold
        self._max_size = max_size

    async def run(
        self, query: str, project_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        filter_fn = (
            (lambda d: d.meta.get("project_id") == project_id) if project_id else None
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
                "question": doc.content,
                "sql": doc.meta.get("sql", ""),
            }
            for doc in filtered
        ]
