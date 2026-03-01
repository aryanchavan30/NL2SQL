import logging
from typing import Any

from jinja2 import Template
from langchain_groq import ChatGroq

from generation.prompts import SQL_ANSWER_SYSTEM_PROMPT, SQL_ANSWER_USER_TEMPLATE

logger = logging.getLogger("nl2sql")

# Rough estimate: 1 token ≈ 4 chars.  Keep data well under LLM context limit.
_MAX_DATA_CHARS = 24_000  # ~6 000 tokens reserved for data


class AnswerGenerator:
    """Generates a natural-language answer from query + SQL + result data."""

    def __init__(self, llm: ChatGroq):
        self._llm = llm
        self._user_template = Template(SQL_ANSWER_USER_TEMPLATE)

    @staticmethod
    def _truncate_rows(
        rows: list[dict], columns: list[str], max_rows: int = 50
    ) -> tuple[list[dict], int]:
        """Truncate rows to fit within token budget.

        Strategy:
        1. Cap at *max_rows* first.
        2. Serialise incrementally and stop when estimated char budget is hit.

        Returns (truncated_rows, num_rows_used).
        """
        if not rows:
            return rows, 0

        budget = _MAX_DATA_CHARS
        kept: list[dict] = []
        used_chars = 0

        for row in rows[:max_rows]:
            # Cheap per-row size estimate (sum of str representations)
            row_chars = sum(len(str(row.get(c, ""))) for c in columns) + len(columns) * 4
            if used_chars + row_chars > budget and kept:
                break
            kept.append(row)
            used_chars += row_chars

        return kept, len(kept)

    async def run(
        self,
        query: str,
        sql: str,
        columns: list[str],
        rows: list[dict],
        max_rows: int = 50,
    ) -> dict[str, Any]:
        total_rows = len(rows)
        truncated, num_rows_used = self._truncate_rows(rows, columns, max_rows)

        user_prompt = self._user_template.render(
            query=query,
            sql=sql,
            columns=columns,
            rows=truncated,
            truncation_note=num_rows_used < total_rows,
            num_rows_used=num_rows_used,
            total_rows=total_rows,
        )

        try:
            response = await self._llm.ainvoke(
                [
                    {"role": "system", "content": SQL_ANSWER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )

            return {
                "answer": response.content,
                "num_rows_used": num_rows_used,
                "total_rows": total_rows,
            }
        except Exception as e:
            logger.exception(f"Answer generation failed: {e}")
            return {
                "answer": None,
                "num_rows_used": num_rows_used,
                "total_rows": total_rows,
                "error": str(e),
            }
