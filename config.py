from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Provider selection ───────────────────────────────────────────────
    llm_provider: str = "groq"        # groq | openai | azure_openai | ollama
    llm_mode: str = "api"             # api | local  (controls enrichment concurrency)
    embedding_provider: str = "ollama"  # ollama | openai | azure_openai

    # ── Groq (defaults so they're optional when using other providers) ───
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # ── Ollama (defaults so they're optional when using other providers) ─
    ollama_base_url: str = "http://localhost:11434"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_llm_model: str = "llama3.2"

    # ── OpenAI ───────────────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"

    # ── Azure OpenAI ─────────────────────────────────────────────────────
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-06-01"
    azure_openai_llm_deployment: str = ""
    azure_openai_embedding_deployment: str = ""

    # ── Embeddings ───────────────────────────────────────────────────────
    embedding_dimension: int = 768

    # Database (required — no defaults)
    db_type: str  # postgresql | mysql | mssql | snowflake | bigquery | databricks
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_database: str
    db_schema: str
    db_connection_string: str = ""  # Optional override (required for snowflake/bigquery/databricks)

    # FAISS
    faiss_persist_dir: str = "./faiss_indices"

    # Logging
    log_level: str = "INFO"

    # Pipeline thresholds
    column_indexing_batch_size: int = 15
    table_retrieval_size: int = 10
    table_column_retrieval_size: int = 100
    historical_question_similarity_threshold: float = 0.9
    sql_pairs_similarity_threshold: float = 0.7
    sql_pairs_retrieval_max_size: int = 10
    instructions_similarity_threshold: float = 0.7
    instructions_retrieval_max_size: int = 10
    max_sql_correction_retries: int = 3

    # Intent override: set to "SQL" to skip LLM classification and always return TEXT_TO_SQL
    intent_override: str = ""

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

    @model_validator(mode="after")
    def _validate_providers(self):
        """Ensure the selected provider's required fields are set."""
        errors: list[str] = []

        # LLM provider
        if self.llm_provider == "ollama":
            pass  # ollama_base_url has a default; no API key needed
        elif self.llm_provider == "groq":
            if not self.groq_api_key:
                errors.append("GROQ_API_KEY is required when LLM_PROVIDER=groq")
        elif self.llm_provider == "openai":
            if not self.openai_api_key:
                errors.append("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        elif self.llm_provider == "azure_openai":
            if not self.azure_openai_api_key:
                errors.append("AZURE_OPENAI_API_KEY is required when LLM_PROVIDER=azure_openai")
            if not self.azure_openai_endpoint:
                errors.append("AZURE_OPENAI_ENDPOINT is required when LLM_PROVIDER=azure_openai")
            if not self.azure_openai_llm_deployment:
                errors.append("AZURE_OPENAI_LLM_DEPLOYMENT is required when LLM_PROVIDER=azure_openai")
        else:
            errors.append(
                f"Unknown LLM_PROVIDER={self.llm_provider!r}. "
                "Supported: groq, openai, azure_openai, ollama"
            )

        # Embedding provider
        if self.embedding_provider == "ollama":
            pass  # ollama_base_url has a default; no API key needed
        elif self.embedding_provider == "openai":
            if not self.openai_api_key:
                errors.append("OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai")
        elif self.embedding_provider == "azure_openai":
            if not self.azure_openai_api_key:
                errors.append("AZURE_OPENAI_API_KEY is required when EMBEDDING_PROVIDER=azure_openai")
            if not self.azure_openai_endpoint:
                errors.append("AZURE_OPENAI_ENDPOINT is required when EMBEDDING_PROVIDER=azure_openai")
            if not self.azure_openai_embedding_deployment:
                errors.append("AZURE_OPENAI_EMBEDDING_DEPLOYMENT is required when EMBEDDING_PROVIDER=azure_openai")
        else:
            errors.append(
                f"Unknown EMBEDDING_PROVIDER={self.embedding_provider!r}. "
                "Supported: ollama, openai, azure_openai"
            )

        if errors:
            raise ValueError("Provider configuration errors:\n  - " + "\n  - ".join(errors))

        return self

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
