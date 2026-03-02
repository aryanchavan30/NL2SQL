"""MySQL adapter — aiomysql driver via SQLAlchemy async engine."""

from __future__ import annotations

from sqlalchemy import text

from mdl.adapter import DatabaseAdapter
from mdl.schema import MDL, Column, Model, Relationship


MYSQL_TYPE_MAP: dict[str, str] = {
    "int": "INTEGER",
    "smallint": "SMALLINT",
    "mediumint": "INTEGER",
    "bigint": "BIGINT",
    "tinyint": "BOOLEAN",
    "float": "FLOAT",
    "double": "DOUBLE",
    "decimal": "NUMERIC",
    "numeric": "NUMERIC",
    "char": "VARCHAR",
    "varchar": "VARCHAR",
    "text": "TEXT",
    "mediumtext": "TEXT",
    "longtext": "TEXT",
    "tinytext": "TEXT",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "timestamp": "TIMESTAMP",
    "time": "VARCHAR",
    "year": "INTEGER",
    "blob": "BYTEA",
    "json": "JSON",
    "enum": "VARCHAR",
    "set": "VARCHAR",
}


class MySQLAdapter(DatabaseAdapter):
    def get_type_map(self) -> dict[str, str]:
        return MYSQL_TYPE_MAP

    async def validate_sql(self, sql: str) -> tuple[bool, str]:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text(f"EXPLAIN {sql}"))
                return True, ""
        except Exception as e:
            return False, str(e)

    async def introspect(self, schema: str | None = None) -> MDL:
        schema = schema or self._schema
        type_map = self.get_type_map()

        async with self._engine.connect() as conn:
            # 1. Tables
            table_result = await conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ), {"schema": schema})
            table_names = [r[0] for r in table_result.fetchall()]

            models: list[Model] = []
            for table_name in table_names:
                # 2. Columns
                col_result = await conn.execute(text(
                    "SELECT column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table "
                    "ORDER BY ordinal_position"
                ), {"schema": schema, "table": table_name})

                columns = [
                    Column(
                        name=row[0],
                        type=type_map.get(row[1].lower(), row[1].upper()),
                        properties={"description": ""},
                    )
                    for row in col_result.fetchall()
                ]

                # 3. Primary key
                pk_result = await conn.execute(text(
                    "SELECT column_name "
                    "FROM information_schema.key_column_usage "
                    "WHERE table_schema = :schema "
                    "  AND table_name = :table "
                    "  AND constraint_name = 'PRIMARY'"
                ), {"schema": schema, "table": table_name})
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

            # 4. Foreign keys (MySQL uses referential_constraints + key_column_usage)
            fk_result = await conn.execute(text(
                "SELECT rc.constraint_name, "
                "       kcu.table_name AS source_table, "
                "       kcu.column_name AS source_column, "
                "       kcu.referenced_table_name AS target_table, "
                "       kcu.referenced_column_name AS target_column "
                "FROM information_schema.referential_constraints rc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON rc.constraint_name = kcu.constraint_name "
                "  AND rc.constraint_schema = kcu.table_schema "
                "WHERE rc.constraint_schema = :schema "
                "  AND kcu.referenced_table_name IS NOT NULL"
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

        return MDL(
            catalog="mysql",
            schema=schema,
            dataSource="mysql",
            models=models,
            relationships=relationships,
            metrics=[],
            views=[],
        )
