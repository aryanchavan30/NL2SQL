from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Groq LLM
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Ollama Embeddings
    ollama_base_url: str = "http://localhost:11434"
    ollama_embedding_model: str = "nomic-embed-text"
    embedding_dimension: int = 768

    # PostgreSQL
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "postgres"
    pg_password: str = "postgres"
    pg_database: str = "northwind"

    # FAISS
    faiss_persist_dir: str = "./faiss_indices"

    # Logging
    log_level: str = "INFO"

    # Pipeline thresholds
    column_indexing_batch_size: int = 50
    table_retrieval_size: int = 10
    table_column_retrieval_size: int = 100
    historical_question_similarity_threshold: float = 0.9
    sql_pairs_similarity_threshold: float = 0.7
    sql_pairs_retrieval_max_size: int = 10
    instructions_similarity_threshold: float = 0.7
    instructions_retrieval_max_size: int = 10
    max_sql_correction_retries: int = 3

    # Service cache
    ask_cache_maxsize: int = 1_000_000
    ask_cache_ttl: int = 120

    @property
    def pg_dsn(self) -> str:
        return f"postgresql://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"
