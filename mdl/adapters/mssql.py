"""Microsoft SQL Server adapter — aioodbc driver via SQLAlchemy async engine."""

from __future__ import annotations

from sqlalchemy import text

from mdl.adapter import DatabaseAdapter
from mdl.schema import MDL, Column, Model, Relationship


MSSQL_TYPE_MAP: dict[str, str] = {
    "int": "INTEGER",
    "smallint": "SMALLINT",
    "bigint": "BIGINT",
    "tinyint": "SMALLINT",
    "real": "FLOAT",
    "float": "DOUBLE",
    "decimal": "NUMERIC",
    "numeric": "NUMERIC",
    "money": "NUMERIC",
    "smallmoney": "NUMERIC",
    "char": "VARCHAR",
    "varchar": "VARCHAR",
    "nchar": "VARCHAR",
    "nvarchar": "VARCHAR",
    "text": "TEXT",
    "ntext": "TEXT",
    "bit": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "datetime2": "TIMESTAMP",
    "smalldatetime": "TIMESTAMP",
    "datetimeoffset": "TIMESTAMP",
    "time": "VARCHAR",
    "uniqueidentifier": "VARCHAR",
    "binary": "BYTEA",
    "varbinary": "BYTEA",
    "image": "BYTEA",
    "xml": "TEXT",
}


class MSSQLAdapter(DatabaseAdapter):
    def get_type_map(self) -> dict[str, str]:
        return MSSQL_TYPE_MAP

    async def validate_sql(self, sql: str) -> tuple[bool, str]:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SET NOEXEC ON"))
                await conn.execute(text(sql))
                await conn.execute(text("SET NOEXEC OFF"))
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
                    "SELECT col.name "
                    "FROM sys.indexes idx "
                    "JOIN sys.index_columns ic "
                    "  ON idx.object_id = ic.object_id AND idx.index_id = ic.index_id "
                    "JOIN sys.columns col "
                    "  ON ic.object_id = col.object_id AND ic.column_id = col.column_id "
                    "JOIN sys.tables t ON idx.object_id = t.object_id "
                    "JOIN sys.schemas s ON t.schema_id = s.schema_id "
                    "WHERE idx.is_primary_key = 1 "
                    "  AND s.name = :schema AND t.name = :table"
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

            # 4. Foreign keys via sys.* views
            fk_result = await conn.execute(text(
                "SELECT fk.name AS constraint_name, "
                "       tp.name AS source_table, "
                "       cp.name AS source_column, "
                "       tr.name AS target_table, "
                "       cr.name AS target_column "
                "FROM sys.foreign_keys fk "
                "JOIN sys.foreign_key_columns fkc "
                "  ON fk.object_id = fkc.constraint_object_id "
                "JOIN sys.tables tp ON fkc.parent_object_id = tp.object_id "
                "JOIN sys.columns cp "
                "  ON fkc.parent_object_id = cp.object_id "
                "  AND fkc.parent_column_id = cp.column_id "
                "JOIN sys.tables tr ON fkc.referenced_object_id = tr.object_id "
                "JOIN sys.columns cr "
                "  ON fkc.referenced_object_id = cr.object_id "
                "  AND fkc.referenced_column_id = cr.column_id "
                "JOIN sys.schemas s ON tp.schema_id = s.schema_id "
                "WHERE s.name = :schema"
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
            catalog="mssql",
            schema=schema,
            dataSource="mssql",
            models=models,
            relationships=relationships,
            metrics=[],
            views=[],
        )
