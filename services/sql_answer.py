import asyncio
import logging
import uuid
from typing import Any, Optional

import asyncpg
from cachetools import TTLCache

from generation.answer import AnswerGenerator

logger = logging.getLogger("nl2sql")


class SqlAnswerService:
    """Orchestrates: execute SQL → preprocess → generate NL answer."""

    def __init__(
        self,
        answer_generator: AnswerGenerator,
        pg_pool: asyncpg.Pool,
        cache_maxsize: int = 1_000_000,
        cache_ttl: int = 120,
    ):
        self._answer_generator = answer_generator
        self._pg_pool = pg_pool
        self._results: dict[str, dict] = TTLCache(
            maxsize=cache_maxsize, ttl=cache_ttl
        )

    def _set_status(self, query_id: str, status: str, **kwargs):
        current = self._results.get(query_id, {})
        current["status"] = status
        current.update(kwargs)
        self._results[query_id] = current

    async def sql_answer(
        self,
        query: str,
        sql: str,
        sql_data: Optional[dict] = None,
        project_id: Optional[str] = None,
    ) -> str:
        """Submit an answer-generation job. Returns query_id immediately."""
        query_id = str(uuid.uuid4())
        self._set_status(query_id, "generating")
        asyncio.create_task(
            self._run(query_id, query, sql, sql_data)
        )
        return query_id

    async def _run(
        self,
        query_id: str,
        query: str,
        sql: str,
        sql_data: Optional[dict],
    ):
        try:
            # 1. Get data — either from caller or by executing SQL
            if sql_data and "columns" in sql_data and "data" in sql_data:
                columns = sql_data["columns"]
                rows = sql_data["data"]
            else:
                columns, rows = await self._execute_sql(sql)

            # 2. Generate NL answer
            result = await self._answer_generator.run(
                query=query,
                sql=sql,
                columns=columns,
                rows=rows,
            )

            if result.get("error"):
                self._set_status(
                    query_id,
                    "failed",
                    error={"code": "OTHERS", "message": result["error"]},
                )
            else:
                self._set_status(
                    query_id,
                    "finished",
                    answer=result["answer"],
                    num_rows_used=result["num_rows_used"],
                    total_rows=result["total_rows"],
                )

        except Exception as e:
            logger.exception(f"SQL answer pipeline failed: {e}")
            self._set_status(
                query_id,
                "failed",
                error={"code": "OTHERS", "message": str(e)},
            )

    async def _execute_sql(self, sql: str) -> tuple[list[str], list[dict]]:
        """Execute SQL against PG and return (columns, rows)."""
        async with self._pg_pool.acquire() as conn:
            stmt = await conn.prepare(sql)
            records = await stmt.fetch()

            if not records:
                return [], []

            columns = [a.name for a in stmt.get_attributes()]
            rows = [dict(r) for r in records]
            return columns, rows

    def get_result(self, query_id: str) -> dict[str, Any]:
        result = self._results.get(query_id)
        if result is None:
            return {
                "status": "failed",
                "error": {"code": "OTHERS", "message": f"{query_id} not found"},
            }
        return result
