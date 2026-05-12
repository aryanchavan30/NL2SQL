import logging
from typing import Any, Optional

import orjson
from jinja2 import Template
from langchain_core.language_models import BaseChatModel

from generation.prompts import (
    INTENT_CLASSIFICATION_SYSTEM_PROMPT,
    INTENT_CLASSIFICATION_USER_TEMPLATE,
)
from utils.helpers import clean_up_new_lines

logger = logging.getLogger("nl2sql")


class IntentClassifier:
    """Classifies user query intent: TEXT_TO_SQL | MISLEADING_QUERY | GENERAL."""

    def __init__(self, llm: BaseChatModel, force_sql: bool = False):
        self._llm = llm
        self._force_sql = force_sql
        self._user_template = Template(INTENT_CLASSIFICATION_USER_TEMPLATE)

    async def run(
        self,
        query: str,
        db_schemas: list[str],
        histories: Optional[list[dict]] = None,
        sql_samples: Optional[list[dict]] = None,
        instructions: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        user_prompt = self._user_template.render(
            query=query,
            db_schemas=db_schemas,
            histories=histories or [],
            sql_samples=sql_samples or [],
            instructions=instructions or [],
        )
        user_prompt = clean_up_new_lines(user_prompt)

        try:
            response = await self._llm.ainvoke(
                [
                    {"role": "system", "content": INTENT_CLASSIFICATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )

            result = orjson.loads(response.content)
            intent = "TEXT_TO_SQL" if self._force_sql else result.get("results", "TEXT_TO_SQL")
            return {
                "rephrased_question": result.get("rephrased_question", query),
                "intent": intent,
                "reasoning": result.get("reasoning", ""),
            }
        except Exception as e:
            logger.warning(f"Intent classification failed, defaulting to TEXT_TO_SQL: {e}")
            return {
                "rephrased_question": query,
                "intent": "TEXT_TO_SQL",
                "reasoning": "",
            }
