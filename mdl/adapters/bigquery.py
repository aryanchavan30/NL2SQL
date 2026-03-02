"""BigQuery adapter — sync driver wrapped via SyncDatabaseAdapter."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from mdl.adapter import SyncDatabaseAdapter
from mdl.schema import MDL, Column, Model


BQ_TYPE_MAP: dict[str, str] = {
    "STRING": "VARCHAR",
    "BYTES": "BYTEA",
    "INT64": "BIGINT",
    "INTEGER": "BIGINT",
    "FLOAT": "DOUBLE",
    "FLOAT64": "DOUBLE",
    "NUMERIC": "NUMERIC",
    "BIGNUMERIC": "NUMERIC",
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
    "TIMESTAMP": "TIMESTAMP",
    "DATE": "DATE",
    "TIME": "VARCHAR",
    "DATETIME": "TIMESTAMP",
    "GEOGRAPHY": "VARCHAR",
    "JSON": "JSON",
    "STRUCT": "JSON",
    "ARRAY": "JSON",
    "RECORD": "JSON",
}


class BigQueryAdapter(SyncDatabaseAdapter):
    def get_type_map(self) -> dict[str, str]:
        return BQ_TYPE_MAP

    async def validate_sql(self, sql: str) -> tuple[bool, str]:
        """Validate by attempting a dry-run (catch errors)."""
        try:
            await self.execute_sql(f"SELECT * FROM ({sql}) AS _validate LIMIT 0")
            return True, ""
        except Exception as e:
            return False, str(e)

    async def introspect(self, schema: str | None = None) -> MDL:
        schema = schema or self._schema
        return await asyncio.to_thread(self._introspect_sync, schema)

    def _introspect_sync(self, schema: str) -> MDL:
        """BigQuery introspection via INFORMATION_SCHEMA with dataset prefix."""
        type_map = self.get_type_map()

        with self._sync_engine.connect() as conn:
            # 1. Tables — BigQuery INFORMATION_SCHEMA is scoped to the dataset
            table_result = conn.execute(text(
                f"SELECT table_name FROM `{schema}`.INFORMATION_SCHEMA.TABLES "
                f"WHERE table_type = 'BASE TABLE' "
                f"ORDER BY table_name"
            ))
            table_names = [r[0] for r in table_result.fetchall()]

            models: list[Model] = []
            for table_name in table_names:
                # 2. Columns
                col_result = conn.execute(text(
                    f"SELECT column_name, data_type, is_nullable "
                    f"FROM `{schema}`.INFORMATION_SCHEMA.COLUMNS "
                    f"WHERE table_name = :table "
                    f"ORDER BY ordinal_position"
                ), {"table": table_name})

                columns = [
                    Column(
                        name=row[0],
                        type=type_map.get(row[1].upper(), row[1].upper()),
                        properties={"description": ""},
                    )
                    for row in col_result.fetchall()
                ]

                # BigQuery has no native primary keys
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

        # BigQuery has no FK constraints — empty relationships
        return MDL(
            catalog="bigquery",
            schema=schema,
            dataSource="bigquery",
            models=models,
            relationships=[],
            metrics=[],
            views=[],
        )
