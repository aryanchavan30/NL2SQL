from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from mdl.schema import MDL

logger = logging.getLogger("nl2sql")


class DatabaseAdapter(ABC):
    """Async database adapter backed by a SQLAlchemy AsyncEngine."""

    def __init__(self, engine: AsyncEngine, schema: str = "public"):
        self._engine = engine
        self._schema = schema

    # -- concrete default -------------------------------------------------

    async def execute_sql(self, sql: str) -> tuple[list[str], list[dict]]:
        """Execute *sql* and return ``(column_names, rows_as_dicts)``."""
        async with self._engine.connect() as conn:
            result = await conn.execute(text(sql))
            columns = list(result.keys())
            rows = [dict(row._mapping) for row in result.fetchall()]
            return columns, rows

    async def close(self) -> None:
        await self._engine.dispose()

    # -- abstract ---------------------------------------------------------

    @abstractmethod
    async def introspect(self, schema: str | None = None) -> MDL:
        """Inspect the database and return a populated MDL object."""

    @abstractmethod
    async def validate_sql(self, sql: str) -> tuple[bool, str]:
        """Validate *sql* without executing it. Return ``(ok, error_msg)``."""

    @abstractmethod
    def get_type_map(self) -> dict[str, str]:
        """Return a mapping from native DB types to canonical MDL types."""


class SyncDatabaseAdapter(DatabaseAdapter):
    """Adapter for drivers that only support synchronous connections.

    Wraps a *sync* SQLAlchemy ``Engine`` and delegates blocking calls to
    ``asyncio.to_thread`` so callers can ``await`` them normally.
    """

    def __init__(self, dsn: str, schema: str = "public"):
        # We still keep a *sync* engine internally.
        from sqlalchemy import create_engine

        self._sync_engine = create_engine(dsn)
        self._schema = schema
        # Store None for async engine — we override all methods anyway.
        self._engine = None  # type: ignore[assignment]

    async def execute_sql(self, sql: str) -> tuple[list[str], list[dict]]:
        return await asyncio.to_thread(self._execute_sync, sql)

    def _execute_sync(self, sql: str) -> tuple[list[str], list[dict]]:
        with self._sync_engine.connect() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = [dict(row._mapping) for row in result.fetchall()]
            return columns, rows

    async def close(self) -> None:
        await asyncio.to_thread(self._sync_engine.dispose)


# -- Factory --------------------------------------------------------------


def create_adapter(settings) -> DatabaseAdapter:
    """Instantiate the correct adapter subclass based on ``settings.db_type``."""
    db_type = settings.db_type.lower()

    if db_type == "postgresql":
        from mdl.adapters.postgresql import PostgreSQLAdapter

        engine = create_async_engine(
            settings.db_dsn,
            pool_size=5,
            max_overflow=5,
        )
        return PostgreSQLAdapter(engine=engine, schema=settings.db_schema)

    if db_type == "mysql":
        from mdl.adapters.mysql import MySQLAdapter

        engine = create_async_engine(
            settings.db_dsn,
            pool_size=5,
            max_overflow=5,
        )
        return MySQLAdapter(engine=engine, schema=settings.db_schema or settings.db_database)

    if db_type == "mssql":
        from mdl.adapters.mssql import MSSQLAdapter

        engine = create_async_engine(
            settings.db_dsn,
            pool_size=5,
            max_overflow=5,
        )
        return MSSQLAdapter(engine=engine, schema=settings.db_schema or "dbo")

    if db_type == "snowflake":
        from mdl.adapters.snowflake import SnowflakeAdapter

        return SnowflakeAdapter(dsn=settings.db_dsn, schema=settings.db_schema)

    if db_type == "bigquery":
        from mdl.adapters.bigquery import BigQueryAdapter

        return BigQueryAdapter(dsn=settings.db_dsn, schema=settings.db_schema)

    if db_type == "databricks":
        from mdl.adapters.databricks import DatabricksAdapter

        return DatabricksAdapter(dsn=settings.db_dsn, schema=settings.db_schema)

    raise ValueError(f"Unsupported db_type: {db_type!r}")
