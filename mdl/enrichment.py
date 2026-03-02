"""LLM-powered MDL description enrichment.

After database introspection produces an MDL with generic descriptions
(e.g. "Table orders", empty column descriptions), this module calls the
LLM to generate meaningful business-context descriptions for every table
and column based on their names, types, relationships, and sample data.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from mdl.adapter import DatabaseAdapter

from mdl.schema import MDL

logger = logging.getLogger("nl2sql")

SYSTEM_PROMPT = (
    "You are a database documentation expert. "
    "Given a table's structure and sample data, generate concise business-context descriptions. "
    "Respond ONLY with a JSON object — no markdown, no explanation.\n\n"
    "Required JSON format:\n"
    "{\n"
    '  "table_description": "1-2 sentence description of what this table stores",\n'
    '  "columns": {\n'
    '    "column_name": "short description of this column"\n'
    "  }\n"
    "}\n\n"
    "Rules:\n"
    "- table_description: 20-200 characters, plain English, describes business meaning\n"
    "- column descriptions: 10-120 characters each, plain English\n"
    "- Do NOT use markdown, special characters, or JSON in the description text\n"
    "- Focus on business meaning, not technical details\n"
    "- Every column in the input must appear in the output"
)


def _build_user_prompt(
    table_name: str,
    columns: list[dict],
    relationships: list[str],
    sample_rows: list[dict] | None,
) -> str:
    """Build the user prompt for a single table."""
    parts = [f"Table: {table_name}\n"]

    parts.append("Columns:")
    for col in columns:
        parts.append(f"  - {col['name']} ({col['type']})")

    if relationships:
        parts.append("\nRelationships:")
        for rel in relationships:
            parts.append(f"  - {rel}")

    if sample_rows:
        parts.append(f"\nSample data ({len(sample_rows)} rows):")
        for i, row in enumerate(sample_rows):
            # Truncate long values
            truncated = {
                k: (str(v)[:80] + "..." if len(str(v)) > 80 else str(v))
                for k, v in row.items()
            }
            parts.append(f"  Row {i + 1}: {truncated}")

    return "\n".join(parts)


def _get_table_relationships(mdl: MDL, table_name: str) -> list[str]:
    """Collect human-readable relationship descriptions for a table."""
    rels = []
    for r in mdl.relationships:
        if table_name in r.models:
            other = r.models[1] if r.models[0] == table_name else r.models[0]
            direction = r.joinType.value.replace("_", " ").lower()
            rels.append(f"{table_name} -> {other} ({direction}): {r.condition}")
    return rels


def _build_sample_query(table_ref: str, db_type: str) -> str:
    """Build a LIMIT-N query appropriate for the database type."""
    if db_type == "mssql":
        return f"SELECT TOP 5 * FROM {table_ref}"
    return f"SELECT * FROM {table_ref} LIMIT 5"


async def enrich_mdl(
    mdl: MDL,
    adapter: "DatabaseAdapter",
    llm: "BaseChatModel",
    db_type: str = "postgresql",
) -> MDL:
    """Enrich all table and column descriptions in *mdl* using the LLM.

    Iterates through each model, fetches sample rows, asks the LLM to
    generate descriptions, and writes them back into the MDL in-place.

    Tables are processed sequentially to stay within Groq rate limits.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    total = len(mdl.models)
    print(f"\n  Enriching descriptions for {total} tables via LLM...")

    for idx, model in enumerate(mdl.models, 1):
        table_name = model.name
        table_ref = model.tableReference or table_name
        print(f"    [{idx}/{total}] {table_name}...", end="", flush=True)

        # 1. Fetch sample rows
        sample_rows: list[dict] | None = None
        try:
            query = _build_sample_query(table_ref, db_type)
            _cols, rows = await adapter.execute_sql(query)
            if rows:
                sample_rows = rows
        except Exception as e:
            logger.warning("Could not fetch sample data for %s: %s", table_name, e)

        # 2. Collect column info and relationships
        columns_info = [{"name": c.name, "type": c.type} for c in model.columns]
        relationships = _get_table_relationships(mdl, table_name)

        # 3. Build prompt and call LLM
        user_prompt = _build_user_prompt(
            table_name, columns_info, relationships, sample_rows,
        )

        try:
            response = await llm.ainvoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=user_prompt),
                ],
                response_format={"type": "json_object"},
            )
            result = json.loads(response.content)
        except Exception as e:
            logger.warning("LLM enrichment failed for %s: %s", table_name, e)
            print(" skipped (LLM error)")
            continue

        # 4. Write descriptions back into the MDL
        table_desc = result.get("table_description", "")
        if table_desc:
            model.properties["description"] = table_desc

        col_descs = result.get("columns", {})
        for col in model.columns:
            desc = col_descs.get(col.name, "")
            if desc:
                col.properties["description"] = desc

        print(" done")

    print("  Enrichment complete.\n")
    return mdl
