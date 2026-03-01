import asyncio
import logging
import uuid
from typing import Any, Optional

from cachetools import TTLCache

from generation.intent import IntentClassifier
from generation.sql_correction import SQLCorrector, SQLValidator
from generation.sql_gen import SQLGenerator
from retrieval.db_schema import DBSchemaRetrieval
from retrieval.historical import HistoricalQuestionRetrieval
from retrieval.instructions import InstructionsRetrieval
from retrieval.sql_pairs import SqlPairsRetrieval

logger = logging.getLogger("nl2sql")


class AskService:
    """
    Orchestrator implementing the ask pipeline state machine:
    UNDERSTANDING -> SEARCHING -> GENERATING -> CORRECTING -> FINISHED/FAILED
    """

    def __init__(
        self,
        historical_retrieval: HistoricalQuestionRetrieval,
        sql_pairs_retrieval: SqlPairsRetrieval,
        instructions_retrieval: InstructionsRetrieval,
        intent_classifier: IntentClassifier,
        db_schema_retrieval: DBSchemaRetrieval,
        sql_generator: SQLGenerator,
        sql_corrector: SQLCorrector,
        sql_validator: SQLValidator,
        max_sql_correction_retries: int = 3,
        cache_maxsize: int = 1_000_000,
        cache_ttl: int = 120,
    ):
        self._historical = historical_retrieval
        self._sql_pairs = sql_pairs_retrieval
        self._instructions = instructions_retrieval
        self._intent_classifier = intent_classifier
        self._db_schema = db_schema_retrieval
        self._sql_generator = sql_generator
        self._sql_corrector = sql_corrector
        self._sql_validator = sql_validator
        self._max_retries = max_sql_correction_retries
        self._results: dict[str, dict] = TTLCache(
            maxsize=cache_maxsize, ttl=cache_ttl
        )

    def _set_status(self, query_id: str, status: str, **kwargs):
        current = self._results.get(query_id, {})
        current["status"] = status
        current.update(kwargs)
        self._results[query_id] = current

    async def ask(
        self,
        query: str,
        project_id: Optional[str] = None,
        histories: Optional[list[dict]] = None,
    ) -> str:
        """Submit a question. Returns query_id immediately; runs in background."""
        query_id = str(uuid.uuid4())
        self._set_status(query_id, "understanding")
        asyncio.create_task(
            self._run_pipeline(query_id, query, project_id, histories)
        )
        return query_id

    async def _run_pipeline(
        self,
        query_id: str,
        query: str,
        project_id: Optional[str],
        histories: Optional[list[dict]],
    ):
        try:
            # ── UNDERSTANDING ────────────────────────────────────────────────
            self._set_status(query_id, "understanding")

            # 1. Historical question retrieval
            historical_results = await self._historical.run(query, project_id)
            if historical_results:
                result = historical_results[0]
                self._set_status(
                    query_id,
                    "finished",
                    type="view" if result.get("viewId") else "llm",
                    response=[{
                        "sql": result.get("statement", ""),
                        "type": "view" if result.get("viewId") else "llm",
                        "viewId": result.get("viewId"),
                    }],
                )
                return

            # 2. SQL pairs + Instructions retrieval (parallel)
            sql_samples_task = self._sql_pairs.run(query, project_id)
            instructions_task = self._instructions.run(query, project_id, scope="sql")
            sql_samples, instructions = await asyncio.gather(
                sql_samples_task, instructions_task
            )

            # 3. Intent classification
            # First get a lightweight schema for intent classification
            retrieval_result = await self._db_schema.run(
                query=query,
                project_id=project_id,
                histories=histories,
            )
            documents = retrieval_result.get("retrieval_results", [])
            table_ddls = [doc["table_ddl"] for doc in documents]
            table_names = [doc["table_name"] for doc in documents]

            instruction_texts = [i.get("instruction", "") for i in instructions]

            intent_result = await self._intent_classifier.run(
                query=query,
                db_schemas=table_ddls,
                histories=histories,
                sql_samples=sql_samples,
                instructions=instruction_texts,
            )

            intent = intent_result.get("intent", "TEXT_TO_SQL")
            rephrased_question = intent_result.get("rephrased_question", query)
            intent_reasoning = intent_result.get("reasoning", "")
            user_query = rephrased_question or query

            if intent == "MISLEADING_QUERY":
                self._set_status(
                    query_id,
                    "finished",
                    type="MISLEADING_QUERY",
                    rephrased_question=rephrased_question,
                    intent_reasoning=intent_reasoning,
                )
                return

            if intent == "GENERAL":
                self._set_status(
                    query_id,
                    "finished",
                    type="GENERAL",
                    rephrased_question=rephrased_question,
                    intent_reasoning=intent_reasoning,
                )
                return

            self._set_status(
                query_id,
                "understanding",
                type="TEXT_TO_SQL",
                rephrased_question=rephrased_question,
                intent_reasoning=intent_reasoning,
            )

            # ── SEARCHING ────────────────────────────────────────────────────
            self._set_status(
                query_id,
                "searching",
                type="TEXT_TO_SQL",
                rephrased_question=rephrased_question,
                intent_reasoning=intent_reasoning,
                retrieved_tables=table_names,
                retrieved_ddls=table_ddls,
            )

            if not documents:
                self._set_status(
                    query_id,
                    "failed",
                    type="TEXT_TO_SQL",
                    error={"code": "NO_RELEVANT_DATA", "message": "No relevant data"},
                )
                return

            has_calculated_field = retrieval_result.get("has_calculated_field", False)
            has_metric = retrieval_result.get("has_metric", False)

            # ── GENERATING ───────────────────────────────────────────────────
            self._set_status(
                query_id,
                "generating",
                type="TEXT_TO_SQL",
                rephrased_question=rephrased_question,
                intent_reasoning=intent_reasoning,
                retrieved_tables=table_names,
                retrieved_ddls=table_ddls,
            )

            gen_result = await self._sql_generator.run(
                query=user_query,
                contexts=table_ddls,
                sql_samples=sql_samples,
                instructions=instruction_texts,
                has_calculated_field=has_calculated_field,
                has_metric=has_metric,
            )

            sql = gen_result.get("sql", "")
            if not sql:
                self._set_status(
                    query_id,
                    "failed",
                    type="TEXT_TO_SQL",
                    error={"code": "NO_RELEVANT_SQL", "message": gen_result.get("error", "SQL generation failed")},
                )
                return

            # Validate
            is_valid, error_msg = await self._sql_validator.validate(sql)
            if is_valid:
                self._set_status(
                    query_id,
                    "finished",
                    type="TEXT_TO_SQL",
                    response=[{"sql": sql, "type": "llm"}],
                    rephrased_question=rephrased_question,
                    intent_reasoning=intent_reasoning,
                    retrieved_tables=table_names,
                    retrieved_ddls=table_ddls,
                )
                return

            # ── CORRECTING ───────────────────────────────────────────────────
            current_sql = sql
            current_error = error_msg

            for retry in range(self._max_retries):
                self._set_status(
                    query_id,
                    "correcting",
                    type="TEXT_TO_SQL",
                    rephrased_question=rephrased_question,
                    intent_reasoning=intent_reasoning,
                    retrieved_tables=table_names,
                    retrieved_ddls=table_ddls,
                )

                correction_result = await self._sql_corrector.run(
                    invalid_sql=current_sql,
                    error=current_error,
                    contexts=table_ddls,
                    instructions=instruction_texts,
                )

                corrected_sql = correction_result.get("sql", "")
                if not corrected_sql:
                    continue

                is_valid, error_msg = await self._sql_validator.validate(corrected_sql)
                if is_valid:
                    self._set_status(
                        query_id,
                        "finished",
                        type="TEXT_TO_SQL",
                        response=[{"sql": corrected_sql, "type": "llm"}],
                        rephrased_question=rephrased_question,
                        intent_reasoning=intent_reasoning,
                        retrieved_tables=table_names,
                        retrieved_ddls=table_ddls,
                    )
                    return

                current_sql = corrected_sql
                current_error = error_msg

            # All retries exhausted
            self._set_status(
                query_id,
                "failed",
                type="TEXT_TO_SQL",
                error={"code": "NO_RELEVANT_SQL", "message": current_error or "SQL correction failed"},
                invalid_sql=current_sql,
                rephrased_question=rephrased_question,
                intent_reasoning=intent_reasoning,
                retrieved_tables=table_names,
                retrieved_ddls=table_ddls,
            )

        except Exception as e:
            logger.exception(f"Ask pipeline failed: {e}")
            self._set_status(
                query_id,
                "failed",
                type="TEXT_TO_SQL",
                error={"code": "OTHERS", "message": str(e)},
            )

    def get_result(self, query_id: str) -> dict[str, Any]:
        result = self._results.get(query_id)
        if result is None:
            return {
                "status": "failed",
                "error": {"code": "OTHERS", "message": f"{query_id} not found"},
            }
        return result

    def stop(self, query_id: str):
        self._set_status(query_id, "stopped")
