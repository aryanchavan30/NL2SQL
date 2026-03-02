from urllib.parse import quote_plus

from pydantic import model_validator
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

    # Database — generic, multi-DB settings
    db_type: str = "postgresql"  # postgresql | mysql | mssql | snowflake | bigquery | databricks
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str = "postgres"
    db_database: str = "northwind"
    db_schema: str = "public"
    db_connection_string: str = ""  # Full override (required for snowflake/bigquery/databricks)

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

    @model_validator(mode="before")
    @classmethod
    def _backward_compat_pg_vars(cls, values: dict) -> dict:
        """Map old PG_* env vars to new db_* fields as fallback."""
        mapping = {
            "pg_host": "db_host",
            "pg_port": "db_port",
            "pg_user": "db_user",
            "pg_password": "db_password",
            "pg_database": "db_database",
        }
        for old, new in mapping.items():
            if old in values and new not in values:
                values[new] = values[old]
        return values

    @property
    def db_dsn(self) -> str:
        if self.db_connection_string:
            return self.db_connection_string
        driver_map = {
            "postgresql": "postgresql+asyncpg",
            "mysql": "mysql+aiomysql",
            "mssql": "mssql+aioodbc",
        }
        driver = driver_map.get(self.db_type, "postgresql+asyncpg")
        password = quote_plus(self.db_password)
        return f"{driver}://{self.db_user}:{password}@{self.db_host}:{self.db_port}/{self.db_database}"
