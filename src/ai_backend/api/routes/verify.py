"""Verification endpoints."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from ai_backend.api.schemas import VerifyAcceptedResponse, VerifyRequest, VerifyResponse
from ai_backend.graph.builder import verification_graph
from ai_backend.graph.state import FinalReport, GraphState
from ai_backend.models.claim import ClaimModel, FinalReportModel, VerificationResultModel
from ai_backend.storage import (
    get_verify_job_result,
    get_verify_job_status,
    mark_verify_job_accepted,
    mark_verify_job_processing,
    save_verify_job_error,
    save_verify_job_result,
)

router = APIRouter(prefix="/verify", tags=["verify"])
logger = logging.getLogger(__name__)


def _document_id(request: VerifyRequest) -> str:
    if request.document_id is None:
        raise ValueError("project_file_id or document_id is required")
    return request.document_id


def _initial_state(request: VerifyRequest) -> GraphState:
    return GraphState(
        raw_text=request.text,
        document_id=_document_id(request),
        run_mode="service",
        document_citations=[citation.to_typed_dict() for citation in request.document_citations],
        claims=[],
        questions=[],
        fact_results=[],
        source_results=[],
        recency_results=[],
        numeric_results=[],
        label="Not Enough Evidence",
        justification="",
        final_grade="확인 필요",
        final_report=FinalReport(final_grade="확인 필요", summary="", issues=[]),
    )


def _response_from_state(state: GraphState, request: VerifyRequest) -> VerifyResponse:
    results = [
        *state["fact_results"],
        *state["source_results"],
        *state["recency_results"],
        *state["numeric_results"],
    ]
    return VerifyResponse(
        project_file_id=request.project_file_id,
        document_id=state["document_id"],
        claims=[ClaimModel.from_typed_dict(claim) for claim in state["claims"]],
        results=[VerificationResultModel.from_typed_dict(result) for result in results],
        final_grade=state["final_grade"],
        final_report=FinalReportModel.from_typed_dict(state["final_report"]),
    )


async def run_verify_job(job_id: str, request: VerifyRequest) -> None:
    """Run verification and persist terminal state.

    The main backend receives an accepted response immediately. The completed
    output is written through the storage boundary for shared-DB polling.
    """
    started = perf_counter()
    logger.info(
        "verify job started job_id=%s project_file_id=%s document_id=%s text_chars=%d",
        job_id,
        request.project_file_id,
        _document_id(request),
        len(request.text),
    )
    try:
        await mark_verify_job_processing(job_id)
        graph_started = perf_counter()
        logger.info("verify graph invoke started job_id=%s", job_id)
        result_state = cast(GraphState, await verification_graph.ainvoke(_initial_state(request)))
        logger.info(
            "verify graph invoke finished job_id=%s elapsed=%.2fs claims=%d results=%d",
            job_id,
            perf_counter() - graph_started,
            len(result_state["claims"]),
            len(
                result_state["fact_results"]
                + result_state["source_results"]
                + result_state["recency_results"]
                + result_state["numeric_results"]
            ),
        )
        result = _response_from_state(result_state, request)
        await save_verify_job_result(job_id, result)
        logger.info(
            "verify job completed job_id=%s elapsed=%.2fs final_grade=%s issues=%d",
            job_id,
            perf_counter() - started,
            result.final_grade,
            len(result.final_report.issues),
        )
    except Exception as exc:
        logger.exception(
            "verify job failed job_id=%s elapsed=%.2fs",
            job_id,
            perf_counter() - started,
        )
        await save_verify_job_error(job_id, exc)


@router.post(
    "",
    response_model=VerifyAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_verify_job(
    request: VerifyRequest,
    background_tasks: BackgroundTasks,
) -> VerifyAcceptedResponse:
    """Accept a document verification request and process it in the background."""
    job_id = request.request_id or f"verify-{uuid4().hex}"
    logger.info(
        "verify job accepted job_id=%s project_file_id=%s document_id=%s text_chars=%d",
        job_id,
        request.project_file_id,
        _document_id(request),
        len(request.text),
    )
    await mark_verify_job_accepted(job_id, request)
    background_tasks.add_task(run_verify_job, job_id, request)
    return VerifyAcceptedResponse(
        job_id=job_id,
        project_file_id=request.project_file_id,
        document_id=_document_id(request),
        request_id=request.request_id,
    )


@router.get("/{job_id}/status")
async def read_verify_job_status(job_id: str) -> dict[str, object]:
    """Development-only status read until the main backend polls the shared DB."""
    job_status = await get_verify_job_status(job_id)
    if job_status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="verify job not found")
    return job_status


@router.get("/{job_id}/result", response_model=VerifyResponse)
async def read_verify_job_result(job_id: str) -> VerifyResponse:
    """Development-only result read until shared DB persistence is implemented."""
    job_status = await get_verify_job_status(job_id)
    if job_status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="verify job not found")
    if job_status["status"] == "failed":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=job_status.get("error") or "verify job failed",
        )
    result = await get_verify_job_result(job_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail="verify job is not completed yet",
        )
    return result
