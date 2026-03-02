"""Databricks adapter — sync driver wrapped via SyncDatabaseAdapter."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from mdl.adapter import SyncDatabaseAdapter
from mdl.schema import MDL, Column, Model, Relationship


DATABRICKS_TYPE_MAP: dict[str, str] = {
    "STRING": "VARCHAR",
    "VARCHAR": "VARCHAR",
    "CHAR": "VARCHAR",
    "BINARY": "BYTEA",
    "BOOLEAN": "BOOLEAN",
    "BYTE": "SMALLINT",
    "TINYINT": "SMALLINT",
    "SHORT": "SMALLINT",
    "SMALLINT": "SMALLINT",
    "INT": "INTEGER",
    "INTEGER": "INTEGER",
    "LONG": "BIGINT",
    "BIGINT": "BIGINT",
    "FLOAT": "FLOAT",
    "DOUBLE": "DOUBLE",
    "DECIMAL": "NUMERIC",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
    "TIMESTAMP_NTZ": "TIMESTAMP",
    "STRUCT": "JSON",
    "ARRAY": "JSON",
    "MAP": "JSON",
}


class DatabricksAdapter(SyncDatabaseAdapter):
    def get_type_map(self) -> dict[str, str]:
        return DATABRICKS_TYPE_MAP

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
            # 1. Tables — Databricks uses information_schema with catalog prefix
            table_result = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_type = 'MANAGED' "
                "   OR table_type = 'EXTERNAL' "
                "ORDER BY table_name"
            ), {"schema": schema})
            table_names = [r[0] for r in table_result.fetchall()]

            models: list[Model] = []
            for table_name in table_names:
                # 2. Columns
                col_result = conn.execute(text(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table "
                    "ORDER BY ordinal_position"
                ), {"schema": schema, "table": table_name})

                columns = [
                    Column(
                        name=row[0],
                        type=type_map.get(row[1].upper(), row[1].upper()),
                        properties={"description": ""},
                    )
                    for row in col_result.fetchall()
                ]

                models.append(Model(
                    name=table_name,
                    tableReference=f"{schema}.{table_name}",
                    primaryKey="",
                    columns=columns,
                    properties={
                        "displayName": table_name.replace("_", " ").title(),
                        "description": f"Table {table_name}",
                    },
                ))

            # 3. Foreign keys (Databricks Unity Catalog supports FK constraints)
            try:
                fk_result = conn.execute(text(
                    "SELECT rc.constraint_name, "
                    "       kcu.table_name AS source_table, "
                    "       kcu.column_name AS source_column, "
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
                ), {"schema": schema})

                relationships = [
                    Relationship(
                        name=row[0],
                        models=[row[1], row[3]],
                        joinType="MANY_TO_ONE",
                        condition=f"{row[1]}.{row[2]} = {row[3]}.{row[4]}",
                    )
                    for row in fk_result.fetchall()
                ]
            except Exception:
                # FK constraints may not be available on all Databricks tiers
                relationships = []

        return MDL(
            catalog="databricks",
            schema=schema,
            dataSource="databricks",
            models=models,
            relationships=relationships,
            metrics=[],
            views=[],
        )
