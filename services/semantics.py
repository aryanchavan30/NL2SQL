import logging
from typing import Any, Optional

from cachetools import TTLCache

from indexing.pipeline import IndexingPipeline, ProgressCallback
from indexing.store import FAISSStoreManager

logger = logging.getLogger("nl2sql")


class SemanticsPreparationService:
    """Manages MDL indexing lifecycle."""

    def __init__(
        self,
        indexing_pipeline: IndexingPipeline,
        store_manager: FAISSStoreManager,
        maxsize: int = 1_000_000,
        ttl: int = 120,
    ):
        self._pipeline = indexing_pipeline
        self._store_manager = store_manager
        self._statuses: dict[str, dict[str, Any]] = TTLCache(
            maxsize=maxsize, ttl=ttl
        )

    async def prepare(
        self,
        mdl_json: str,
        mdl_hash: str,
        project_id: Optional[str] = None,
        sql_pairs: Optional[list[dict[str, str]]] = None,
        on_progress: Optional[ProgressCallback] = None,
    ):
        self._statuses[mdl_hash] = {"status": "indexing"}

        try:
            await self._pipeline.run(
                mdl_str=mdl_json,
                project_id=project_id,
                sql_pairs=sql_pairs,
                on_progress=on_progress,
            )
            self._statuses[mdl_hash] = {"status": "finished"}
            logger.info(f"Semantics preparation finished for mdl_hash={mdl_hash}")
        except Exception as e:
            logger.exception(f"Semantics preparation failed: {e}")
            self._statuses[mdl_hash] = {
                "status": "failed",
                "error": {"code": "OTHERS", "message": str(e)},
            }

    def get_status(self, mdl_hash: str) -> dict[str, Any]:
        result = self._statuses.get(mdl_hash)
        if result is None:
            return {
                "status": "failed",
                "error": {"code": "OTHERS", "message": f"{mdl_hash} not found"},
            }
        return result

    async def delete(self, project_id: str):
        for name in ["db_schema", "table_descriptions", "view_questions", "sql_pairs", "instructions", "project_meta"]:
            store = self._store_manager.get_store(name)
            store.delete_documents(
                lambda d: d.meta.get("project_id") == project_id
            )
        self._store_manager.save_all()
        logger.info(f"Deleted semantics data for project_id={project_id}")
