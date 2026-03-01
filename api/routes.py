import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from api.models import (
    AskRequest,
    AskResponse,
    AskResultResponse,
    DeleteSemanticsRequest,
    SemanticsPreparationRequest,
    SemanticsPreparationResponse,
    SemanticsPreparationStatusResponse,
    SqlAnswerRequest,
    SqlAnswerResponse,
    SqlAnswerResultResponse,
)

logger = logging.getLogger("nl2sql")

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


# ── Ask Endpoints ────────────────────────────────────────────────────────────


@router.post("/v1/asks", response_model=AskResponse)
async def create_ask(request: AskRequest, req: Request):
    ask_service = req.app.state.ask_service

    histories = [h.model_dump() for h in request.histories] if request.histories else None

    query_id = await ask_service.ask(
        query=request.query,
        project_id=request.project_id,
        histories=histories,
    )

    return AskResponse(query_id=query_id)


@router.get("/v1/asks/{query_id}/result", response_model=AskResultResponse)
async def get_ask_result(query_id: str, req: Request):
    ask_service = req.app.state.ask_service
    result = ask_service.get_result(query_id)
    return AskResultResponse(**result)


@router.patch("/v1/asks/{query_id}")
async def stop_ask(query_id: str, req: Request):
    ask_service = req.app.state.ask_service
    ask_service.stop(query_id)
    return {"query_id": query_id}


# ── Semantics Preparation Endpoints ─────────────────────────────────────────


@router.post(
    "/v1/semantics-preparations",
    response_model=SemanticsPreparationResponse,
)
async def create_semantics_preparation(
    request: SemanticsPreparationRequest,
    background_tasks: BackgroundTasks,
    req: Request,
):
    semantics_service = req.app.state.semantics_service

    # Set initial indexing status
    semantics_service._statuses[request.mdl_hash] = {"status": "indexing"}

    background_tasks.add_task(
        semantics_service.prepare,
        mdl_json=request.mdl,
        mdl_hash=request.mdl_hash,
        project_id=request.project_id,
        sql_pairs=request.sql_pairs if request.sql_pairs else None,
    )

    return SemanticsPreparationResponse(mdl_hash=request.mdl_hash)


@router.get(
    "/v1/semantics-preparations/{mdl_hash}/status",
    response_model=SemanticsPreparationStatusResponse,
)
async def get_semantics_preparation_status(mdl_hash: str, req: Request):
    semantics_service = req.app.state.semantics_service
    result = semantics_service.get_status(mdl_hash)
    return SemanticsPreparationStatusResponse(**result)


# ── SQL Answer Endpoints ────────────────────────────────────────────────────


@router.post("/v1/sql-answers", response_model=SqlAnswerResponse)
async def create_sql_answer(request: SqlAnswerRequest, req: Request):
    sql_answer_service = req.app.state.sql_answer_service

    query_id = await sql_answer_service.sql_answer(
        query=request.query,
        sql=request.sql,
        sql_data=request.sql_data,
        project_id=request.project_id,
    )

    return SqlAnswerResponse(query_id=query_id)


@router.get("/v1/sql-answers/{query_id}/result", response_model=SqlAnswerResultResponse)
async def get_sql_answer_result(query_id: str, req: Request):
    sql_answer_service = req.app.state.sql_answer_service
    result = sql_answer_service.get_result(query_id)
    return SqlAnswerResultResponse(**result)


# ── Semantics Preparation Endpoints ─────────────────────────────────────────


@router.delete("/v1/semantics")
async def delete_semantics(request: DeleteSemanticsRequest, req: Request):
    semantics_service = req.app.state.semantics_service
    await semantics_service.delete(request.project_id)
    return {"status": "ok"}
