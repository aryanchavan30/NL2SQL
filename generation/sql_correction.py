import logging
from typing import Any, Optional

import orjson
from jinja2 import Template
from langchain_groq import ChatGroq

from generation.prompts import (
    SQL_CORRECTION_SYSTEM_PROMPT,
    SQL_CORRECTION_USER_TEMPLATE,
)
from utils.helpers import clean_generation_result, clean_up_new_lines

logger = logging.getLogger("nl2sql")


class SQLValidator:
    """Validates SQL via the configured DatabaseAdapter."""

    def __init__(self, adapter):
        self._adapter = adapter

    async def validate(self, sql: str) -> tuple[bool, str]:
        return await self._adapter.validate_sql(sql)


class SQLCorrector:
    """Corrects invalid SQL using Groq LLM."""

    def __init__(self, llm: ChatGroq):
        self._llm = llm
        self._user_template = Template(SQL_CORRECTION_USER_TEMPLATE)

    async def run(
        self,
        invalid_sql: str,
        error: str,
        contexts: list[str],
        instructions: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        user_prompt = self._user_template.render(
            documents=contexts,
            instructions=instructions or [],
            invalid_generation_result={
                "sql": invalid_sql,
                "error": error,
            },
        )
        user_prompt = clean_up_new_lines(user_prompt)

        try:
            response = await self._llm.ainvoke(
                [
                    {"role": "system", "content": SQL_CORRECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )

            content = response.content
            cleaned = clean_generation_result(content)

            if cleaned.startswith("{"):
                result = orjson.loads(cleaned)
                sql = result.get("sql", cleaned)
            else:
                sql = cleaned

            logger.info(f"Corrected SQL: {sql}")
            return {"sql": sql, "error": None}

        except Exception as e:
            logger.exception(f"SQL correction failed: {e}")
            return {"sql": "", "error": str(e)}
