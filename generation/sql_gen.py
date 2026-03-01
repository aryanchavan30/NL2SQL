import logging
from typing import Any, Optional

import orjson
from jinja2 import Template
from langchain_groq import ChatGroq

from generation.prompts import (
    CALCULATED_FIELD_INSTRUCTIONS,
    METRIC_INSTRUCTIONS,
    SQL_GENERATION_SYSTEM_PROMPT,
    SQL_GENERATION_USER_TEMPLATE,
)
from utils.helpers import clean_generation_result, clean_up_new_lines

logger = logging.getLogger("nl2sql")


class SQLGenerator:
    """Generates SQL from natural language using Groq LLM."""

    def __init__(self, llm: ChatGroq):
        self._llm = llm
        self._user_template = Template(SQL_GENERATION_USER_TEMPLATE)

    async def run(
        self,
        query: str,
        contexts: list[str],
        sql_samples: Optional[list[dict]] = None,
        instructions: Optional[list[str]] = None,
        has_calculated_field: bool = False,
        has_metric: bool = False,
    ) -> dict[str, Any]:
        user_prompt = self._user_template.render(
            query=query,
            documents=contexts,
            sql_samples=sql_samples or [],
            instructions=instructions or [],
            calculated_field_instructions=CALCULATED_FIELD_INSTRUCTIONS if has_calculated_field else "",
            metric_instructions=METRIC_INSTRUCTIONS if has_metric else "",
        )
        user_prompt = clean_up_new_lines(user_prompt)

        try:
            response = await self._llm.ainvoke(
                [
                    {"role": "system", "content": SQL_GENERATION_SYSTEM_PROMPT},
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

            logger.info(f"Generated SQL: {sql}")
            return {"sql": sql, "error": None}

        except Exception as e:
            logger.exception(f"SQL generation failed: {e}")
            return {"sql": "", "error": str(e)}
