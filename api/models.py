from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ── Ask ──────────────────────────────────────────────────────────────────────

class AskHistory(BaseModel):
    sql: str
    question: str


class AskRequest(BaseModel):
    query: str
    project_id: Optional[str] = None
    mdl_hash: Optional[str] = None
    histories: list[AskHistory] = Field(default_factory=list)


class AskResponse(BaseModel):
    query_id: str


class AskResult(BaseModel):
    sql: str
    type: Literal["llm", "view"] = "llm"
    viewId: Optional[str] = None


class AskError(BaseModel):
    code: Literal["NO_RELEVANT_DATA", "NO_RELEVANT_SQL", "OTHERS"]
    message: str


class AskResultResponse(BaseModel):
    status: Literal[
        "understanding",
        "searching",
        "generating",
        "correcting",
        "finished",
        "failed",
        "stopped",
    ]
    type: Optional[str] = None
    rephrased_question: Optional[str] = None
    intent_reasoning: Optional[str] = None
    retrieved_tables: Optional[list[str]] = None
    response: Optional[list[AskResult]] = None
    invalid_sql: Optional[str] = None
    error: Optional[AskError] = None


# ── Semantics Preparation ────────────────────────────────────────────────────

class SemanticsPreparationRequest(BaseModel):
    mdl: str
    mdl_hash: str
    project_id: Optional[str] = None
    sql_pairs: list[dict[str, str]] = Field(default_factory=list)


class SemanticsPreparationResponse(BaseModel):
    mdl_hash: str


class SemanticsPreparationStatusResponse(BaseModel):
    class SemanticsError(BaseModel):
        code: Literal["OTHERS"]
        message: str

    status: Literal["indexing", "finished", "failed"]
    error: Optional[SemanticsError] = None


class DeleteSemanticsRequest(BaseModel):
    project_id: str


# ── SQL Answer ──────────────────────────────────────────────────────────────


class SqlAnswerRequest(BaseModel):
    query: str
    sql: str
    sql_data: Optional[dict] = None  # {"columns": [...], "data": [...]}
    project_id: Optional[str] = None


class SqlAnswerResponse(BaseModel):
    query_id: str


class SqlAnswerResultResponse(BaseModel):
    status: Literal["generating", "finished", "failed"]
    answer: Optional[str] = None
    num_rows_used: Optional[int] = None
    total_rows: Optional[int] = None
    error: Optional[AskError] = None
