"""Persistence boundary for main-backend shared DB integration.

The actual table names and columns are owned by the main Django backend and are
not fixed yet. Keep all DB writes behind these functions so the API route does
not change when the shared schema is decided.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from ai_backend.api.schemas import VerifyRequest, VerifyResponse

logger = logging.getLogger(__name__)

VerifyJobStatus = Literal["accepted", "processing", "completed", "failed"]

_job_statuses: dict[str, VerifyJobStatus] = {}
_job_results: dict[str, VerifyResponse] = {}
_job_errors: dict[str, str] = {}


async def mark_verify_job_accepted(job_id: str, request: VerifyRequest) -> None:
    """Persist the initial accepted state for a verification job.

    Replace this body with a write to the shared DB once the Django-side schema
    is fixed. The route already passes every field needed for that write.
    """
    _job_statuses[job_id] = "accepted"
    _job_results.pop(job_id, None)
    _job_errors.pop(job_id, None)
    logger.info(
        "verify job accepted",
        extra={
            "job_id": job_id,
            "document_id": request.document_id,
            "project_file_id": request.project_file_id,
            "project_id": request.project_id,
            "request_id": request.request_id,
        },
    )


async def mark_verify_job_processing(job_id: str) -> None:
    """Persist that background verification has started."""
    _job_statuses[job_id] = "processing"
    logger.info("verify job processing", extra={"job_id": job_id})


async def save_verify_job_result(job_id: str, result: VerifyResponse) -> None:
    """Persist completed verification output to the shared DB.

    The current implementation intentionally does not choose a DB schema. When
    the contract is fixed, map ``result.final_report.issues`` to Django's
    ``core_filereviewitem`` rows:

    - ``project_file_id``: ``result.project_file_id``
    - ``highlighted_text``: issue original text
    - ``problem``: issue reason
    - ``suggestion``: issue suggestion
    - ``order``: issue index
    """
    _job_statuses[job_id] = "completed"
    _job_results[job_id] = result
    logger.info(
        "verify job completed",
        extra={
            "job_id": job_id,
            "document_id": result.document_id,
            "project_file_id": result.project_file_id,
            "claim_count": len(result.claims),
            "result_count": len(result.results),
            "final_grade": result.final_grade,
        },
    )


async def save_verify_job_error(job_id: str, exc: BaseException) -> None:
    """Persist a failed terminal state for a verification job."""
    _job_statuses[job_id] = "failed"
    _job_errors[job_id] = str(exc)
    logger.exception(
        "verify job failed",
        extra={"job_id": job_id, "error": str(exc)},
    )


async def get_verify_job_status(job_id: str) -> dict[str, Any] | None:
    """Return lightweight status for local development before DB polling exists."""
    status = _job_statuses.get(job_id)
    if status is None:
        return None
    response: dict[str, Any] = {"job_id": job_id, "status": status}
    if status == "failed":
        response["error"] = _job_errors.get(job_id, "")
    return response


async def get_verify_job_result(job_id: str) -> VerifyResponse | None:
    """Return completed verification output for local development."""
    return _job_results.get(job_id)
