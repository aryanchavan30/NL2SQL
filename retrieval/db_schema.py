import ast
import logging
from typing import Any, Optional

from langchain_core.embeddings import Embeddings

from indexing.store import FAISSStoreManager
from utils.helpers import build_metric_ddl, build_table_ddl, build_view_ddl

logger = logging.getLogger("nl2sql")


class DBSchemaRetrieval:
    """2-phase retrieval: table discovery -> schema fetch -> DDL construction."""

    def __init__(
        self,
        store_manager: FAISSStoreManager,
        embeddings: Embeddings,
        table_retrieval_size: int = 10,
        table_column_retrieval_size: int = 100,
    ):
        self._table_desc_store = store_manager.get_store("table_descriptions")
        self._db_schema_store = store_manager.get_store("db_schema")
        self._embeddings = embeddings
        self._table_retrieval_size = table_retrieval_size
        self._table_column_retrieval_size = table_column_retrieval_size

    async def run(
        self,
        query: str,
        project_id: Optional[str] = None,
        histories: Optional[list[dict]] = None,
    ) -> dict[str, Any]:
        # Phase 1: Table discovery via table_descriptions
        previous_questions = []
        if histories:
            previous_questions = [h.get("question", "") for h in histories]

        search_query = "\n".join(previous_questions) + "\n" + query if previous_questions else query
        query_embedding = await self._embeddings.aembed_query(search_query)

        table_filter = None
        if project_id:
            table_filter = lambda d: (
                d.meta.get("type") == "TABLE_DESCRIPTION"
                and d.meta.get("project_id") == project_id
            )
        else:
            table_filter = lambda d: d.meta.get("type") == "TABLE_DESCRIPTION"

        table_docs = self._table_desc_store.search_by_embedding(
            query_embedding=query_embedding,
            top_k=self._table_retrieval_size,
            filter_fn=table_filter,
        )

        # Extract table names
        table_names = []
        for doc in table_docs:
            try:
                content = ast.literal_eval(doc.content)
                table_names.append(content["name"])
            except Exception:
                if doc.meta.get("name"):
                    table_names.append(doc.meta["name"])

        if not table_names:
            return {
                "retrieval_results": [],
                "has_calculated_field": False,
                "has_metric": False,
                "has_json_field": False,
            }

        logger.info(f"Phase 1 - discovered tables: {table_names}")

        # Phase 2: Schema fetch from db_schema store
        schema_filter = None
        if project_id:
            schema_filter = lambda d: (
                d.meta.get("type") == "TABLE_SCHEMA"
                and d.meta.get("name") in table_names
                and d.meta.get("project_id") == project_id
            )
        else:
            schema_filter = lambda d: (
                d.meta.get("type") == "TABLE_SCHEMA"
                and d.meta.get("name") in table_names
            )

        schema_docs = self._db_schema_store.search_by_filter(schema_filter)

        # Phase 3: Merge TABLE + TABLE_COLUMNS per table and build DDLs
        db_schemas: dict[str, dict] = {}
        for doc in schema_docs:
            try:
                content = ast.literal_eval(doc.content)
            except Exception:
                continue

            name = doc.meta.get("name", "")
            if content.get("type") == "TABLE":
                if name not in db_schemas:
                    db_schemas[name] = content
                else:
                    db_schemas[name] = {
                        **content,
                        "columns": db_schemas[name].get("columns", []),
                    }
            elif content.get("type") == "TABLE_COLUMNS":
                if name not in db_schemas:
                    db_schemas[name] = {"columns": content.get("columns", [])}
                else:
                    existing_cols = db_schemas[name].get("columns", [])
                    db_schemas[name]["columns"] = existing_cols + content.get("columns", [])
            elif content.get("type") in ("METRIC", "VIEW"):
                db_schemas[name] = content

        # Remove incomplete schemas
        db_schemas = {
            k: v for k, v in db_schemas.items()
            if "type" in v and ("columns" in v or v["type"] in ("VIEW",))
        }

        retrieval_results = []
        has_calculated_field = False
        has_metric = False
        has_json_field = False

        for table_schema in db_schemas.values():
            schema_type = table_schema.get("type")
            if schema_type == "TABLE":
                ddl, _has_calc, _has_json = build_table_ddl(table_schema)
                retrieval_results.append({
                    "table_name": table_schema["name"],
                    "table_ddl": ddl,
                })
                if _has_calc:
                    has_calculated_field = True
                if _has_json:
                    has_json_field = True
            elif schema_type == "METRIC":
                retrieval_results.append({
                    "table_name": table_schema["name"],
                    "table_ddl": build_metric_ddl(table_schema),
                })
                has_metric = True
            elif schema_type == "VIEW":
                retrieval_results.append({
                    "table_name": table_schema["name"],
                    "table_ddl": build_view_ddl(table_schema),
                })

        return {
            "retrieval_results": retrieval_results,
            "has_calculated_field": has_calculated_field,
            "has_metric": has_metric,
            "has_json_field": has_json_field,
        }
