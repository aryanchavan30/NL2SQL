import re
from typing import Optional

from indexing.store import Document


def get_engine_supported_data_type(data_type: str) -> str:
    upper = data_type.upper()
    mapping = {
        "BPCHAR": "VARCHAR",
        "NAME": "VARCHAR",
        "UUID": "VARCHAR",
        "INET": "VARCHAR",
        "OID": "INT",
        "BIGNUMERIC": "NUMERIC",
        "BYTES": "BYTEA",
        "DATETIME": "TIMESTAMP",
        "FLOAT64": "DOUBLE",
        "INT64": "BIGINT",
    }
    return mapping.get(upper, upper)


def build_table_ddl(
    content: dict,
    columns: Optional[set[str]] = None,
    tables: Optional[set[str]] = None,
) -> tuple[str, bool, bool]:
    """Build DDL string from a TABLE_SCHEMA content dict.

    Returns (ddl_string, has_calculated_field, has_json_field).
    """
    columns_ddl = []
    has_calculated_field = False
    has_json_field = False

    for column in content.get("columns", []):
        if column.get("type") == "COLUMN":
            if (
                (not columns or column["name"] in columns)
                and column.get("data_type", "").lower() != "unknown"
            ):
                if "This column is a Calculated Field" in column.get("comment", ""):
                    has_calculated_field = True
                if column.get("data_type", "").lower() == "json":
                    has_json_field = True
                col_ddl = (
                    f"{column.get('comment', '')}{column['name']} "
                    f"{get_engine_supported_data_type(column.get('data_type', ''))}"
                )
                if column.get("is_primary_key"):
                    col_ddl += " PRIMARY KEY"
                columns_ddl.append(col_ddl)
        elif column.get("type") == "FOREIGN_KEY":
            if not tables or set(column.get("tables", [])).issubset(tables):
                columns_ddl.append(
                    f"{column.get('comment', '')}{column.get('constraint', '')}"
                )

    ddl = (
        f"{content.get('comment', '')}CREATE TABLE {content['name']} (\n  "
        + ",\n  ".join(columns_ddl)
        + "\n);"
    )
    return ddl, has_calculated_field, has_json_field


def build_metric_ddl(content: dict) -> str:
    columns_ddl = [
        f"{col['comment']}{col['name']} {get_engine_supported_data_type(col['data_type'])}"
        for col in content.get("columns", [])
        if col.get("data_type", "").lower() != "unknown"
    ]
    return (
        f"{content.get('comment', '')}CREATE TABLE {content['name']} (\n  "
        + ",\n  ".join(columns_ddl)
        + "\n);"
    )


def build_view_ddl(content: dict) -> str:
    return (
        f"{content.get('comment', '')}CREATE VIEW {content['name']}\n"
        f"AS {content.get('statement', '')}"
    )


def score_filter(
    documents: list[Document],
    threshold: float = 0.9,
    max_size: int = 10,
) -> list[Document]:
    return sorted(
        (doc for doc in documents if doc.score >= threshold),
        key=lambda d: d.score,
        reverse=True,
    )[:max_size]


MULTIPLE_NEW_LINE_REGEX = re.compile(r"\n{3,}")


def clean_up_new_lines(text: str) -> str:
    return MULTIPLE_NEW_LINE_REGEX.sub("\n\n\n", text)


def clean_generation_result(result: str) -> str:
    def _normalize_whitespace(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    return (
        _normalize_whitespace(result)
        .replace("```sql", "")
        .replace("```json", "")
        .replace('"""', "")
        .replace("'''", "")
        .replace("```", "")
        .replace(";", "")
    )
