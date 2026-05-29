"""API request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_backend.graph.state import FinalGrade, Label, VerifierName
from ai_backend.models.claim import (
    CitationModel,
    ClaimModel,
    FinalReportModel,
    QuestionModel,
    VerificationResultModel,
)


class VerifyRequest(BaseModel):
    """Document verification request."""

    model_config = ConfigDict(extra="forbid")

    project_file_id: int | None = Field(
        default=None,
        description="Django core.ProjectFile id. Preferred id for shared-DB integration.",
    )
    document_id: str | None = Field(
        default=None,
        description="Legacy/free-form document identifier. Defaults to project_file_id.",
    )
    request_id: str | None = Field(
        default=None,
        description="Optional idempotency/request id from the main backend",
    )
    project_id: int | None = Field(default=None, description="Optional Django core.Project id")
    topic: str | None = Field(default=None, description="Optional ProjectFile topic/title")
    text: str = Field(min_length=1, description="Raw document text to verify")
    document_citations: list[CitationModel] = Field(
        default_factory=list,
        description="Document-level sources such as original URL, PDF URL, or references",
    )

    @model_validator(mode="after")
    def require_django_or_legacy_document_id(self) -> VerifyRequest:
        if self.project_file_id is None and not self.document_id:
            raise ValueError("project_file_id or document_id is required")
        if self.document_id is None and self.project_file_id is not None:
            self.document_id = str(self.project_file_id)
        return self


class VerifyAcceptedResponse(BaseModel):
    """Immediate response returned after a verification job is accepted."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    project_file_id: int | None = None
    document_id: str
    request_id: str | None = None
    status: Literal["accepted"] = "accepted"


class VerifyResponse(BaseModel):
    """Document verification response."""

    model_config = ConfigDict(extra="forbid")

    project_file_id: int | None = None
    document_id: str
    claims: list[ClaimModel]
    results: list[VerificationResultModel]
    final_grade: FinalGrade
    final_report: FinalReportModel


class AveritecPrediction(BaseModel):
    """Prediction shape consumed by the AVeriTeC evaluator."""

    model_config = ConfigDict(extra="forbid")

    label: Label
    questions: list[QuestionModel]
    justification: str


class AveritecVerifyResponse(AveritecPrediction):
    """Internal/evaluation response with document metadata included."""

    project_file_id: int | None = None
    document_id: str
    claims: list[ClaimModel]


class RecheckRequest(BaseModel):
    """Request to recheck a specific claim."""

    model_config = ConfigDict(extra="forbid")

    parent_claim_id: str
    document_id: str
    text: str
    context: str
    verifiers: list[VerifierName] | None = None


class RecheckResponse(BaseModel):
    """Recheck response with the new claim and verifier results."""

    model_config = ConfigDict(extra="forbid")

    new_claim: ClaimModel
    results: list[VerificationResultModel]


class ErrorResponse(BaseModel):
    """Error response compatible with FastAPI's detail shape."""

    detail: str
    error_code: str | None = None
