import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.routes import router
from config import Settings
from providers import create_llm, create_embeddings
from generation.intent import IntentClassifier
from generation.sql_correction import SQLCorrector, SQLValidator
from generation.answer import AnswerGenerator
from generation.sql_gen import SQLGenerator
from indexing.pipeline import IndexingPipeline
from indexing.store import FAISSStoreManager
from mdl.adapter import create_adapter
from retrieval.db_schema import DBSchemaRetrieval
from retrieval.historical import HistoricalQuestionRetrieval
from retrieval.instructions import InstructionsRetrieval
from retrieval.sql_pairs import SqlPairsRetrieval
from services.ask import AskService
from services.semantics import SemanticsPreparationService
from services.sql_answer import SqlAnswerService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger("nl2sql")
    logger.info("Starting NL2SQL service...")

    # Init LLM
    llm = create_llm(settings)
    logger.info(f"LLM initialized: provider={settings.llm_provider}")

    # Init embeddings
    embeddings = create_embeddings(settings)
    logger.info(f"Embeddings initialized: provider={settings.embedding_provider}")

    # Init FAISS store manager
    store_manager = FAISSStoreManager(
        dimension=settings.embedding_dimension,
        persist_dir=settings.faiss_persist_dir,
    )
    store_manager.load_all()
    logger.info("FAISS stores loaded")

    # Init database adapter
    adapter = create_adapter(settings)
    logger.info(f"Database adapter created: {settings.db_type}")

    # Init indexing pipeline
    indexing_pipeline = IndexingPipeline(
        store_manager=store_manager,
        embeddings=embeddings,
        column_batch_size=settings.column_indexing_batch_size,
    )

    # Init retrieval pipelines
    historical_retrieval = HistoricalQuestionRetrieval(
        store_manager=store_manager,
        embeddings=embeddings,
        similarity_threshold=settings.historical_question_similarity_threshold,
    )
    sql_pairs_retrieval = SqlPairsRetrieval(
        store_manager=store_manager,
        embeddings=embeddings,
        similarity_threshold=settings.sql_pairs_similarity_threshold,
        max_size=settings.sql_pairs_retrieval_max_size,
    )
    instructions_retrieval = InstructionsRetrieval(
        store_manager=store_manager,
        embeddings=embeddings,
        similarity_threshold=settings.instructions_similarity_threshold,
        max_size=settings.instructions_retrieval_max_size,
    )
    db_schema_retrieval = DBSchemaRetrieval(
        store_manager=store_manager,
        embeddings=embeddings,
        table_retrieval_size=settings.table_retrieval_size,
        table_column_retrieval_size=settings.table_column_retrieval_size,
    )

    # Init generation components
    intent_classifier = IntentClassifier(llm=llm)
    sql_generator = SQLGenerator(llm=llm)
    sql_corrector = SQLCorrector(llm=llm)
    sql_validator = SQLValidator(adapter=adapter)

    # Init services
    ask_service = AskService(
        historical_retrieval=historical_retrieval,
        sql_pairs_retrieval=sql_pairs_retrieval,
        instructions_retrieval=instructions_retrieval,
        intent_classifier=intent_classifier,
        db_schema_retrieval=db_schema_retrieval,
        sql_generator=sql_generator,
        sql_corrector=sql_corrector,
        sql_validator=sql_validator,
        max_sql_correction_retries=settings.max_sql_correction_retries,
        cache_maxsize=settings.ask_cache_maxsize,
        cache_ttl=settings.ask_cache_ttl,
    )

    semantics_service = SemanticsPreparationService(
        indexing_pipeline=indexing_pipeline,
        store_manager=store_manager,
        maxsize=settings.ask_cache_maxsize,
        ttl=settings.ask_cache_ttl,
    )

    answer_generator = AnswerGenerator(llm=llm)
    sql_answer_service = SqlAnswerService(
        answer_generator=answer_generator,
        adapter=adapter,
        cache_maxsize=settings.ask_cache_maxsize,
        cache_ttl=settings.ask_cache_ttl,
    )

    # Attach to app state
    app.state.settings = settings
    app.state.ask_service = ask_service
    app.state.semantics_service = semantics_service
    app.state.sql_answer_service = sql_answer_service
    app.state.adapter = adapter
    app.state.store_manager = store_manager

    logger.info("All services initialized. Ready to serve requests.")

    yield

    # Shutdown
    logger.info("Shutting down NL2SQL service...")
    store_manager.save_all()
    await adapter.close()
    logger.info("Shutdown complete.")


app = FastAPI(title="NL2SQL Pipeline", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
