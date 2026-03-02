"""
Interactive NL2SQL CLI — Ask questions in natural language, get SQL back.

Usage:
    python run.py                          # Auto-introspects DB, builds MDL using mdl/schema.py
    python run.py --mdl path/to/mdl.json   # Load MDL from file on startup
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from langchain_core.language_models import BaseChatModel

from config import Settings
from providers import create_llm, create_embeddings
from generation.answer import AnswerGenerator
from generation.intent import IntentClassifier
from generation.sql_correction import SQLCorrector, SQLValidator
from generation.sql_gen import SQLGenerator
from indexing.pipeline import IndexingPipeline
from indexing.store import FAISSStoreManager
from mdl.adapter import create_adapter
from mdl.schema import MDL
from retrieval.db_schema import DBSchemaRetrieval
from retrieval.historical import HistoricalQuestionRetrieval
from retrieval.instructions import InstructionsRetrieval
from retrieval.sql_pairs import SqlPairsRetrieval
from services.ask import AskService
from services.semantics import SemanticsPreparationService


class NL2SQLEngine:
    """All-in-one engine for interactive NL2SQL.

    Delegates to AskService and SemanticsPreparationService — the same services
    used by the FastAPI app in main.py — so behaviour is identical.
    """

    def __init__(self):
        self.settings = Settings()
        self.store_manager = None
        self.adapter = None
        self.llm: BaseChatModel | None = None
        self.ask_service: AskService | None = None
        self.semantics_service: SemanticsPreparationService | None = None
        self.answer_generator: AnswerGenerator | None = None
        self._indexed = False
        self._current_mdl: MDL | None = None
        self._histories: list[dict] = []

    async def initialize(self):
        """Initialize all components (mirrors main.py lifespan wiring)."""
        s = self.settings

        print(f"[1/5] Initializing LLM ({s.llm_provider})...")
        llm = create_llm(s)
        self.llm = llm

        print(f"[2/5] Initializing Embeddings ({s.embedding_provider})...")
        embeddings = create_embeddings(s)
        try:
            await embeddings.aembed_query("test")
        except Exception as e:
            print(f"\n  ERROR: Cannot connect to embedding provider ({s.embedding_provider})")
            if s.embedding_provider == "ollama":
                print(f"  Make sure Ollama is running: ollama serve")
                print(f"  And model is pulled: ollama pull {s.ollama_embedding_model}")
            print(f"  Error: {e}")
            sys.exit(1)

        print("[3/5] Initializing FAISS stores...")
        self.store_manager = FAISSStoreManager(
            dimension=s.embedding_dimension,
            persist_dir=s.faiss_persist_dir,
        )
        self.store_manager.load_all()

        print(f"[4/5] Connecting to database ({s.db_type})...")
        try:
            self.adapter = create_adapter(s)
            # Quick connectivity check
            await self.adapter.execute_sql("SELECT 1")
            print(f"  Connected to {s.db_type}: {s.db_host}:{s.db_port}/{s.db_database}")
        except Exception as e:
            print(f"\n  ERROR: Cannot connect to {s.db_type} at {s.db_host}:{s.db_port}/{s.db_database}")
            print(f"  Error: {e}")
            sys.exit(1)

        print("[5/5] Wiring services...")

        # ── Build components (same order as main.py lifespan) ────────────────
        indexing_pipeline = IndexingPipeline(
            store_manager=self.store_manager,
            embeddings=embeddings,
            column_batch_size=s.column_indexing_batch_size,
        )

        historical_retrieval = HistoricalQuestionRetrieval(
            store_manager=self.store_manager,
            embeddings=embeddings,
            similarity_threshold=s.historical_question_similarity_threshold,
        )
        sql_pairs_retrieval = SqlPairsRetrieval(
            store_manager=self.store_manager,
            embeddings=embeddings,
            similarity_threshold=s.sql_pairs_similarity_threshold,
            max_size=s.sql_pairs_retrieval_max_size,
        )
        instructions_retrieval = InstructionsRetrieval(
            store_manager=self.store_manager,
            embeddings=embeddings,
            similarity_threshold=s.instructions_similarity_threshold,
            max_size=s.instructions_retrieval_max_size,
        )
        db_schema_retrieval = DBSchemaRetrieval(
            store_manager=self.store_manager,
            embeddings=embeddings,
            table_retrieval_size=s.table_retrieval_size,
            table_column_retrieval_size=s.table_column_retrieval_size,
        )

        intent_classifier = IntentClassifier(llm=llm)
        sql_generator = SQLGenerator(llm=llm)
        self.answer_generator = AnswerGenerator(llm=llm)
        sql_corrector = SQLCorrector(llm=llm)
        sql_validator = SQLValidator(adapter=self.adapter)

        # ── Wire AskService (identical to main.py) ──────────────────────────
        self.ask_service = AskService(
            historical_retrieval=historical_retrieval,
            sql_pairs_retrieval=sql_pairs_retrieval,
            instructions_retrieval=instructions_retrieval,
            intent_classifier=intent_classifier,
            db_schema_retrieval=db_schema_retrieval,
            sql_generator=sql_generator,
            sql_corrector=sql_corrector,
            sql_validator=sql_validator,
            max_sql_correction_retries=s.max_sql_correction_retries,
            cache_maxsize=s.ask_cache_maxsize,
            cache_ttl=s.ask_cache_ttl,
        )

        # ── Wire SemanticsPreparationService (identical to main.py) ─────────
        self.semantics_service = SemanticsPreparationService(
            indexing_pipeline=indexing_pipeline,
            store_manager=self.store_manager,
            maxsize=s.ask_cache_maxsize,
            ttl=s.ask_cache_ttl,
        )

        print()
        print("All systems ready!")

    # ── MDL Loading ─────────────────────────────────────────────────────────

    async def index_mdl_file(self, mdl_path: str):
        """Load MDL JSON file, validate through mdl/schema.py, and index."""
        print(f"\nLoading MDL from: {mdl_path}")
        with open(mdl_path, "r", encoding="utf-8") as f:
            raw = json.loads(f.read())

        # Validate through our Pydantic MDL model
        mdl = MDL.model_validate(raw)
        self._current_mdl = mdl

        print(f"  MDL validated successfully:")
        print(f"    Models:        {len(mdl.models)}")
        for m in mdl.models:
            print(f"      - {m.name} ({len(m.columns)} columns, pk={m.primaryKey})")
        print(f"    Relationships: {len(mdl.relationships)}")
        for r in mdl.relationships:
            print(f"      - {r.name}: {r.models[0]} -> {r.models[1]} ({r.joinType.value})")
        print(f"    Metrics:       {len(mdl.metrics)}")
        print(f"    Views:         {len(mdl.views)}")
        print()

        # Serialize validated MDL back to JSON for indexing pipeline
        mdl_json = mdl.model_dump_json(by_alias=True)
        await self._run_indexing(mdl_json)

    async def index_from_db(self):
        """Auto-introspect database and build MDL via the adapter."""
        print("\nIntrospecting database to build MDL...")
        mdl = await self.adapter.introspect(schema=self.settings.db_schema)

        print(f"  Built MDL with {len(mdl.models)} models, {len(mdl.relationships)} relationships:")
        for m in mdl.models:
            print(f"    - {m.name} ({len(m.columns)} columns, pk={m.primaryKey})")

        # Enrich descriptions via LLM
        from mdl.enrichment import enrich_mdl

        mdl = await enrich_mdl(
            mdl, self.adapter, self.llm, db_type=self.settings.db_type,
        )
        self._current_mdl = mdl

        # Save MDL JSON for reference/reuse
        mdl_json = mdl.model_dump_json(by_alias=True, indent=2)
        mdl_dir = os.path.join(os.path.dirname(__file__), "mdl")
        os.makedirs(mdl_dir, exist_ok=True)
        mdl_path = os.path.join(mdl_dir, f"{self.settings.db_database}_mdl.json")
        with open(mdl_path, "w") as f:
            f.write(mdl_json)
        print(f"  MDL saved to: {mdl_path}")
        print(f"  (You can edit this file and reload with --mdl {mdl_path})")
        print()

        await self._run_indexing(mdl.model_dump_json(by_alias=True))

    async def _run_indexing(self, mdl_json: str):
        """Run indexing via SemanticsPreparationService (same as API route)."""
        from tqdm import tqdm

        print("Indexing...", flush=True)
        start = time.time()

        # Progress bar state
        _pbar: tqdm | None = None
        _current_stage: str | None = None

        def _on_progress(stage: str, done: int, total: int):
            nonlocal _pbar, _current_stage
            if stage != _current_stage:
                # Close previous bar
                if _pbar is not None:
                    _pbar.close()
                    _pbar = None
                _current_stage = stage
                if total > 0:
                    _pbar = tqdm(
                        total=total, desc=f"  {stage}",
                        unit="docs", leave=True, ncols=72,
                        bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
                    )
                else:
                    print(f"  {stage}: 0 docs (skipped)")
            if _pbar is not None and done > _pbar.n:
                _pbar.update(done - _pbar.n)

        mdl_hash = hashlib.md5(mdl_json.encode()).hexdigest()
        await self.semantics_service.prepare(
            mdl_json=mdl_json,
            mdl_hash=mdl_hash,
            project_id="default",
            on_progress=_on_progress,
        )

        if _pbar is not None:
            _pbar.close()

        status = self.semantics_service.get_status(mdl_hash)
        elapsed = time.time() - start

        if status["status"] == "failed":
            error_info = status.get("error", {})
            print(f"\n  INDEXING FAILED: {error_info.get('message', 'Unknown error')}")
            print("  Common causes:")
            if self.settings.embedding_provider == "ollama":
                print("    - Ollama not running: ollama serve")
                print(f"    - Model not pulled: ollama pull {self.settings.ollama_embedding_model}")
            print("    - Embedding input too long: reduce COLUMN_INDEXING_BATCH_SIZE in .env")
            return

        print(f"  done in {elapsed:.1f}s")

        for name in ["db_schema", "table_descriptions", "view_questions", "sql_pairs"]:
            store = self.store_manager.get_store(name)
            count = store.count_documents()
            if count > 0:
                print(f"  {name}: {count} documents")

        self._indexed = True
        print()

    # ── Ask ──────────────────────────────────────────────────────────────────

    async def ask(self, question: str) -> dict:
        """Ask a question via AskService (same pipeline as API route).

        Submits to AskService.ask() which runs the full pipeline
        (historical → sql_pairs + instructions → db_schema → intent →
        sql_gen → validate → correct) as a background task, then polls
        until the result is ready.
        """
        query_id = await self.ask_service.ask(
            query=question,
            project_id="default",
            histories=self._histories if self._histories else None,
        )

        # Poll until the background pipeline completes
        while True:
            result = self.ask_service.get_result(query_id)
            status = result.get("status", "")
            if status in ("finished", "failed", "stopped"):
                break
            await asyncio.sleep(0.05)

        return result

    async def execute_sql(self, sql: str) -> tuple[list[str], list[dict]]:
        """Execute SQL and return (columns, rows)."""
        return await self.adapter.execute_sql(sql)

    async def generate_answer(
        self, query: str, sql: str, columns: list[str], rows: list[dict]
    ) -> dict:
        """Generate a natural-language answer from query + SQL + data."""
        return await self.answer_generator.run(
            query=query, sql=sql, columns=columns, rows=rows
        )

    async def shutdown(self):
        self.store_manager.save_all()
        if self.adapter:
            await self.adapter.close()


# ── Display Helpers ─────────────────────────────────────────────────────────


def print_banner():
    print("=" * 60)
    print("  NL2SQL — Ask your database in natural language")
    print("=" * 60)
    print()


def print_retrieved_schema(ddls: list[str]):
    print()
    print("  Retrieved Schema:")
    print("  " + "=" * 50)
    for ddl in ddls:
        for line in ddl.strip().split("\n"):
            print(f"  {line}")
        print("  " + "-" * 50)
    print()


def print_answer(answer: str, num_rows_used: int, total_rows: int):
    print()
    print("  Answer:")
    print("  " + "\u2500" * 50)
    for line in answer.strip().split("\n"):
        print(f"  {line}")
    if num_rows_used < total_rows:
        print(f"\n  (Based on {num_rows_used} of {total_rows} rows)")
    print("  " + "\u2500" * 50)


def print_sql(sql: str):
    print()
    print("  SQL:")
    print("  " + "-" * 50)
    for line in sql.strip().split("\n"):
        print(f"  {line}")
    print("  " + "-" * 50)


def print_results(rows: list[dict], max_rows: int = 20):
    if not rows:
        print("  (no rows returned)")
        return

    columns = list(rows[0].keys())
    widths = {col: max(len(str(col)), max(len(str(row.get(col, ""))) for row in rows[:max_rows])) for col in columns}
    widths = {col: min(w, 40) for col, w in widths.items()}

    header = " | ".join(str(col).ljust(widths[col])[:widths[col]] for col in columns)
    print(f"  {header}")
    print(f"  {'-' * len(header)}")

    for row in rows[:max_rows]:
        line = " | ".join(str(row.get(col, "")).ljust(widths[col])[:widths[col]] for col in columns)
        print(f"  {line}")

    if len(rows) > max_rows:
        print(f"  ... and {len(rows) - max_rows} more rows")

    print(f"\n  ({len(rows)} row{'s' if len(rows) != 1 else ''} total)")


def extract_sql_from_result(result: dict) -> str | None:
    """Extract SQL string from AskService result format."""
    response = result.get("response")
    if response and isinstance(response, list) and len(response) > 0:
        return response[0].get("sql", "")
    # Also check invalid_sql for failed corrections
    return result.get("invalid_sql")


# ── Main Loop ───────────────────────────────────────────────────────────────


async def main():
    print_banner()

    engine = NL2SQLEngine()
    await engine.initialize()

    # Check if MDL file provided via args
    mdl_path = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--mdl" and i < len(sys.argv):
            mdl_path = sys.argv[i + 1]

    # Check if already indexed
    db_schema_store = engine.store_manager.get_store("db_schema")
    already_indexed = db_schema_store.count_documents() > 0

    if mdl_path:
        await engine.index_mdl_file(mdl_path)
    elif already_indexed:
        count = db_schema_store.count_documents()
        print(f"Found existing index with {count} schema documents. Using cached index.")
        print("  (Use --mdl <path> to re-index, or type 'reindex' to rebuild from DB)")
        print()
        engine._indexed = True
    else:
        print("No indexed schema found. Auto-introspecting database via MDL models...")
        await engine.index_from_db()

    if not engine._indexed:
        print("ERROR: No schema indexed. Cannot proceed.")
        await engine.shutdown()
        return

    # Interactive loop
    print("Type your question in natural language. Commands:")
    print("  exit / quit          — Exit")
    print("  reindex              — Re-introspect DB and rebuild MDL index")
    print("  run                  — Execute the last generated SQL")
    print("  tables               — Show indexed tables")
    print("  mdl                  — Show current MDL summary")
    print("  history              — Show conversation history")
    print("  clear                — Clear conversation history")
    print()

    last_sql = None

    while True:
        try:
            question = input("Question> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question:
            continue

        if question.lower() in ("exit", "quit", "q"):
            break

        if question.lower() == "reindex":
            await engine.index_from_db()
            engine._histories.clear()
            continue

        if question.lower() == "mdl":
            if engine._current_mdl:
                mdl = engine._current_mdl
                print(f"\n  Current MDL:")
                print(f"    Catalog:       {mdl.catalog}")
                print(f"    Schema:        {mdl.schema_}")
                print(f"    Data Source:    {mdl.dataSource}")
                print(f"    Models ({len(mdl.models)}):")
                for m in mdl.models:
                    cols = [c.name for c in m.columns]
                    print(f"      {m.name} (pk={m.primaryKey})")
                    print(f"        columns: {', '.join(cols)}")
                print(f"    Relationships ({len(mdl.relationships)}):")
                for r in mdl.relationships:
                    print(f"      {r.models[0]} -> {r.models[1]} ({r.joinType.value}): {r.condition}")
                print(f"    Metrics:       {len(mdl.metrics)}")
                print(f"    Views:         {len(mdl.views)}")
            else:
                print("  No MDL loaded (using cached index from previous session)")
            print()
            continue

        if question.lower() == "tables":
            import ast
            store = engine.store_manager.get_store("table_descriptions")
            docs = store.search_by_filter(lambda d: True)
            print("\n  Indexed tables:")
            for doc in docs:
                try:
                    content = ast.literal_eval(doc.content)
                    name = content.get("name", "")
                    desc = content.get("description", "")
                    cols = content.get("columns", "")
                    print(f"    - {name}" + (f"  ({desc})" if desc else ""))
                    if cols:
                        print(f"      columns: {cols}")
                except Exception:
                    print(f"    - {doc.meta.get('name', '?')}")
            print()
            continue

        if question.lower() == "history":
            if engine._histories:
                print("\n  Conversation history:")
                for i, h in enumerate(engine._histories, 1):
                    print(f"    {i}. Q: {h['question']}")
                    print(f"       SQL: {h['sql'][:80]}...")
            else:
                print("\n  No conversation history yet.")
            print()
            continue

        if question.lower() == "clear":
            engine._histories.clear()
            print("  Conversation history cleared.\n")
            continue

        if question.lower() == "run":
            if last_sql:
                try:
                    print("\n  Executing SQL...")
                    _cols, rows = await engine.execute_sql(last_sql)
                    print_results(rows)
                    print()
                except Exception as e:
                    print(f"\n  Execution error: {e}\n")
            else:
                print("  No SQL to execute. Ask a question first.\n")
            continue

        # ── Ask the question via AskService ──────────────────────────────────
        print("\n  Thinking...", end="", flush=True)
        start = time.time()

        try:
            result = await engine.ask(question)
        except Exception as e:
            print(f"\r  Error: {e}\n")
            continue

        elapsed = time.time() - start
        status = result.get("status", "")
        result_type = result.get("type", "")

        print(f"\r  Done in {elapsed:.1f}s" + " " * 20)

        if status == "finished" and result_type in ("TEXT_TO_SQL", "llm", "view"):
            sql = extract_sql_from_result(result)
            if sql:
                # Auto-execute SQL and generate NL answer
                try:
                    columns, rows = await engine.execute_sql(sql)
                    if columns and rows:
                        answer_result = await engine.generate_answer(
                            query=question, sql=sql, columns=columns, rows=rows,
                        )
                        if answer_result.get("answer"):
                            print_answer(
                                answer_result["answer"],
                                answer_result.get("num_rows_used", len(rows)),
                                answer_result.get("total_rows", len(rows)),
                            )
                    elif not rows:
                        print("\n  (Query returned no rows)")
                except Exception as e:
                    print(f"\n  (Could not generate answer: {e})")

                print_sql(sql)

                if result.get("retrieved_ddls"):
                    print_retrieved_schema(result["retrieved_ddls"])
                if result.get("retrieved_tables"):
                    print(f"  Tables used: {', '.join(result['retrieved_tables'])}")
                if result.get("rephrased_question"):
                    print(f"  Rephrased: {result['rephrased_question']}")

                last_sql = sql

                # Track conversation history for follow-up questions
                engine._histories.append({
                    "question": question,
                    "sql": sql,
                })

                print()
                print("  Type 'run' to see raw query results.")
            else:
                print("\n  Finished but no SQL in response.")

        elif status == "finished" and result_type == "MISLEADING_QUERY":
            reasoning = result.get("intent_reasoning", "")
            print(f"\n  This question doesn't seem related to the database.")
            if reasoning:
                print(f"  Reasoning: {reasoning}")

        elif status == "finished" and result_type == "GENERAL":
            reasoning = result.get("intent_reasoning", "")
            print(f"\n  This is a general question. Please be more specific about what data you need.")
            if reasoning:
                print(f"  Reasoning: {reasoning}")

        elif status == "failed":
            error = result.get("error", {})
            error_code = error.get("code", "OTHERS") if isinstance(error, dict) else "OTHERS"
            error_msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)

            print(f"\n  Failed ({error_code}): {error_msg}")

            # Show the invalid SQL if correction failed
            invalid_sql = result.get("invalid_sql")
            if invalid_sql:
                print(f"\n  Last attempted SQL:")
                print_sql(invalid_sql)
                last_sql = invalid_sql

            if result.get("retrieved_ddls"):
                print_retrieved_schema(result["retrieved_ddls"])

        elif status == "stopped":
            print("\n  Query was stopped.")

        else:
            print(f"\n  Unexpected result: status={status}, type={result_type}")

        print()

    # Shutdown
    print("Saving indices and shutting down...")
    await engine.shutdown()
    print("Goodbye!")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(main())
