import asyncio
import logging
from typing import Any, Callable, Optional

import orjson
from langchain_core.embeddings import Embeddings

from indexing.chunkers import DDLChunker, SqlPairsConverter, TableDescriptionChunker, ViewChunker
from indexing.store import Document

logger = logging.getLogger("nl2sql")

# Callback signature: (stage_name, docs_done, docs_total) -> None
ProgressCallback = Callable[[str, int, int], None]


class IndexingPipeline:
    """Orchestrates 5 parallel indexing sub-pipelines."""

    def __init__(
        self,
        store_manager,
        embeddings: Embeddings,
        column_batch_size: int = 50,
    ):
        self._store_manager = store_manager
        self._embeddings = embeddings
        self._column_batch_size = column_batch_size
        self._ddl_chunker = DDLChunker()
        self._table_desc_chunker = TableDescriptionChunker()
        self._view_chunker = ViewChunker()
        self._sql_pairs_converter = SqlPairsConverter()

    async def run(
        self,
        mdl_str: str,
        project_id: str | None = None,
        sql_pairs: list[dict[str, str]] | None = None,
        on_progress: Optional[ProgressCallback] = None,
    ):
        mdl_dict = orjson.loads(mdl_str)

        # Ensure required keys
        mdl_dict.setdefault("models", [])
        mdl_dict.setdefault("views", [])
        mdl_dict.setdefault("relationships", [])
        mdl_dict.setdefault("metrics", [])

        if on_progress:
            # Sequential for clean progress reporting (CLI)
            await self._index_db_schema(mdl_dict, project_id, on_progress)
            await self._index_table_descriptions(mdl_dict, project_id, on_progress)
            await self._index_view_questions(mdl_dict, project_id, on_progress)
            await self._index_sql_pairs(sql_pairs or [], project_id, on_progress)
            await self._index_project_meta(mdl_dict, project_id)
        else:
            # Parallel for API throughput
            await asyncio.gather(
                self._index_db_schema(mdl_dict, project_id),
                self._index_table_descriptions(mdl_dict, project_id),
                self._index_view_questions(mdl_dict, project_id),
                self._index_sql_pairs(sql_pairs or [], project_id),
                self._index_project_meta(mdl_dict, project_id),
            )

        self._store_manager.save_all()
        logger.info("Indexing pipeline completed and indices saved.")

    # nomic-embed-text has 8192 token context (~4 chars/token).
    # Cap at 7000 tokens ≈ 28,000 chars to leave margin.
    _MAX_EMBED_CHARS = 28_000
    _EMBED_BATCH_SIZE = 10

    async def _embed_documents(
        self,
        documents: list[Document],
        stage_name: str = "",
        on_progress: Optional[ProgressCallback] = None,
    ) -> list[Document]:
        if not documents:
            if on_progress:
                on_progress(stage_name, 0, 0)
            return documents

        # Truncate oversized content
        contents = []
        for doc in documents:
            text = doc.content
            if len(text) > self._MAX_EMBED_CHARS:
                logger.warning(
                    "Truncating document for embedding: %s (%d chars -> %d)",
                    doc.meta.get("name", "?"),
                    len(text),
                    self._MAX_EMBED_CHARS,
                )
                text = text[: self._MAX_EMBED_CHARS]
            contents.append(text)

        total = len(contents)
        if on_progress:
            on_progress(stage_name, 0, total)

        # Embed in small batches
        all_embeddings = []
        for i in range(0, total, self._EMBED_BATCH_SIZE):
            batch = contents[i : i + self._EMBED_BATCH_SIZE]
            batch_embeddings = await self._embeddings.aembed_documents(batch)
            all_embeddings.extend(batch_embeddings)
            if on_progress:
                on_progress(stage_name, min(i + len(batch), total), total)

        for doc, emb in zip(documents, all_embeddings):
            doc.embedding = emb

        return documents

    async def _index_db_schema(
        self, mdl_dict: dict, project_id: str | None,
        on_progress: Optional[ProgressCallback] = None,
    ):
        store = self._store_manager.get_store("db_schema")

        # Delete old docs for this project
        if project_id:
            store.delete_documents(
                lambda d: d.meta.get("project_id") == project_id
            )

        documents = self._ddl_chunker.run(
            mdl_dict, self._column_batch_size, project_id
        )
        documents = await self._embed_documents(documents, "db_schema", on_progress)
        store.add_documents(documents)
        logger.info(f"Indexed {len(documents)} db_schema documents")

    async def _index_table_descriptions(
        self, mdl_dict: dict, project_id: str | None,
        on_progress: Optional[ProgressCallback] = None,
    ):
        store = self._store_manager.get_store("table_descriptions")

        if project_id:
            store.delete_documents(
                lambda d: d.meta.get("project_id") == project_id
            )

        documents = self._table_desc_chunker.run(mdl_dict, project_id)
        documents = await self._embed_documents(documents, "table_descriptions", on_progress)
        store.add_documents(documents)
        logger.info(f"Indexed {len(documents)} table_description documents")

    async def _index_view_questions(
        self, mdl_dict: dict, project_id: str | None,
        on_progress: Optional[ProgressCallback] = None,
    ):
        store = self._store_manager.get_store("view_questions")

        if project_id:
            store.delete_documents(
                lambda d: d.meta.get("project_id") == project_id
            )

        documents = self._view_chunker.run(mdl_dict, project_id)
        if documents:
            documents = await self._embed_documents(documents, "view_questions", on_progress)
            store.add_documents(documents)
        elif on_progress:
            on_progress("view_questions", 0, 0)
        logger.info(f"Indexed {len(documents)} view_question documents")

    async def _index_sql_pairs(
        self, sql_pairs: list[dict[str, str]], project_id: str | None,
        on_progress: Optional[ProgressCallback] = None,
    ):
        store = self._store_manager.get_store("sql_pairs")

        if project_id:
            store.delete_documents(
                lambda d: d.meta.get("project_id") == project_id
            )

        documents = self._sql_pairs_converter.run(sql_pairs, project_id)
        if documents:
            documents = await self._embed_documents(documents, "sql_pairs", on_progress)
            store.add_documents(documents)
        elif on_progress:
            on_progress("sql_pairs", 0, 0)
        logger.info(f"Indexed {len(documents)} sql_pairs documents")

    async def _index_project_meta(self, mdl_dict: dict, project_id: str | None):
        store = self._store_manager.get_store("project_meta")

        if project_id:
            store.delete_documents(
                lambda d: d.meta.get("project_id") == project_id
            )

        doc = Document(
            content="project_meta",
            meta={
                "project_id": project_id or "",
                "data_source": mdl_dict.get("dataSource", "postgresql"),
            },
        )
        store.add_documents([doc])
        logger.info("Indexed project_meta document")
