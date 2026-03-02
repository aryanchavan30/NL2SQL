import logging
from typing import Any, Optional

from langchain_core.embeddings import Embeddings

from indexing.store import FAISSStoreManager
from utils.helpers import score_filter

logger = logging.getLogger("nl2sql")


class HistoricalQuestionRetrieval:
    """Retrieves similar historical questions from view_questions store."""

    def __init__(
        self,
        store_manager: FAISSStoreManager,
        embeddings: Embeddings,
        similarity_threshold: float = 0.9,
    ):
        self._store = store_manager.get_store("view_questions")
        self._embeddings = embeddings
        self._threshold = similarity_threshold

    async def run(
        self, query: str, project_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        # Check if store has documents
        filter_fn = (
            (lambda d: d.meta.get("project_id") == project_id) if project_id else None
        )
        if self._store.count_documents(filter_fn) == 0:
            return []

        query_embedding = await self._embeddings.aembed_query(query)

        documents = self._store.search_by_embedding(
            query_embedding=query_embedding,
            top_k=10,
            filter_fn=filter_fn,
        )

        filtered = score_filter(documents, self._threshold, max_size=1)

        return [
            {
                "question": doc.content,
                "summary": doc.meta.get("summary", ""),
                "statement": doc.meta.get("statement", ""),
                "viewId": doc.meta.get("viewId", ""),
            }
            for doc in filtered
        ]
