"""Snowflake adapter — sync driver wrapped via SyncDatabaseAdapter."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from mdl.adapter import SyncDatabaseAdapter
from mdl.schema import MDL, Column, Model, Relationship


SNOWFLAKE_TYPE_MAP: dict[str, str] = {
    "NUMBER": "NUMERIC",
    "DECIMAL": "NUMERIC",
    "NUMERIC": "NUMERIC",
    "INT": "INTEGER",
    "INTEGER": "INTEGER",
    "BIGINT": "BIGINT",
    "SMALLINT": "SMALLINT",
    "TINYINT": "SMALLINT",
    "FLOAT": "DOUBLE",
    "FLOAT4": "FLOAT",
    "FLOAT8": "DOUBLE",
    "DOUBLE": "DOUBLE",
    "DOUBLE PRECISION": "DOUBLE",
    "REAL": "FLOAT",
    "VARCHAR": "VARCHAR",
    "CHAR": "VARCHAR",
    "CHARACTER": "VARCHAR",
    "STRING": "VARCHAR",
    "TEXT": "TEXT",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "DATETIME": "TIMESTAMP",
    "TIMESTAMP": "TIMESTAMP",
    "TIMESTAMP_NTZ": "TIMESTAMP",
    "TIMESTAMP_LTZ": "TIMESTAMP",
    "TIMESTAMP_TZ": "TIMESTAMP",
    "TIME": "VARCHAR",
    "BINARY": "BYTEA",
    "VARBINARY": "BYTEA",
    "VARIANT": "JSON",
    "OBJECT": "JSON",
    "ARRAY": "JSON",
}


class SnowflakeAdapter(SyncDatabaseAdapter):
    def get_type_map(self) -> dict[str, str]:
        return SNOWFLAKE_TYPE_MAP

    async def validate_sql(self, sql: str) -> tuple[bool, str]:
        try:
            await self.execute_sql(f"EXPLAIN {sql}")
            return True, ""
        except Exception as e:
            return False, str(e)

    async def introspect(self, schema: str | None = None) -> MDL:
        schema = schema or self._schema
        return await asyncio.to_thread(self._introspect_sync, schema)

    def _introspect_sync(self, schema: str) -> MDL:
        type_map = self.get_type_map()

        with self._sync_engine.connect() as conn:
            # 1. Tables (Snowflake uppercases identifiers)
            table_result = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ), {"schema": schema.upper()})
            table_names = [r[0] for r in table_result.fetchall()]

            models: list[Model] = []
            for table_name in table_names:
                # 2. Columns
                col_result = conn.execute(text(
                    "SELECT column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table "
                    "ORDER BY ordinal_position"
                ), {"schema": schema.upper(), "table": table_name})

                columns = [
                    Column(
                        name=row[0],
                        type=type_map.get(row[1].upper(), row[1].upper()),
                        properties={"description": ""},
                    )
                    for row in col_result.fetchall()
                ]

                # 3. Primary key
                pk_result = conn.execute(text(
                    "SELECT column_name "
                    "FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu "
                    "  ON tc.constraint_name = kcu.constraint_name "
                    "  AND tc.table_schema = kcu.table_schema "
                    "WHERE tc.table_schema = :schema "
                    "  AND tc.table_name = :table "
                    "  AND tc.constraint_type = 'PRIMARY KEY'"
                ), {"schema": schema.upper(), "table": table_name})
                pk_rows = pk_result.fetchall()
                pk_col = pk_rows[0][0] if pk_rows else ""

                models.append(Model(
                    name=table_name,
                    tableReference=f"{schema}.{table_name}",
                    primaryKey=pk_col,
                    columns=columns,
                    properties={
                        "displayName": table_name.replace("_", " ").title(),
                        "description": f"Table {table_name}",
                    },
                ))

            # 4. Foreign keys
            fk_result = conn.execute(text(
                "SELECT rc.constraint_name, "
                "       kcu.table_name AS source_table, "
                "       kcu.column_name AS source_column, "
                "       rc.unique_constraint_name, "
                "       kcu2.table_name AS target_table, "
                "       kcu2.column_name AS target_column "
                "FROM information_schema.referential_constraints rc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON rc.constraint_name = kcu.constraint_name "
                "  AND rc.constraint_schema = kcu.table_schema "
                "JOIN information_schema.key_column_usage kcu2 "
                "  ON rc.unique_constraint_name = kcu2.constraint_name "
                "  AND rc.unique_constraint_schema = kcu2.table_schema "
                "WHERE rc.constraint_schema = :schema"
            ), {"schema": schema.upper()})

            relationships = [
                Relationship(
                    name=row[0],
                    models=[row[1], row[4]],
                    joinType="MANY_TO_ONE",
                    condition=f"{row[1]}.{row[2]} = {row[4]}.{row[5]}",
                )
                for row in fk_result.fetchall()
            ]

        return MDL(
            catalog="snowflake",
            schema=schema,
            dataSource="snowflake",
            models=models,
            relationships=relationships,
            metrics=[],
            views=[],
        )
