"""Pydantic models equivalent to graph state TypedDicts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_backend.graph.state import (
    Citation as CitationDict,
)
from ai_backend.graph.state import (
    CitationType,
    ClaimType,
    FinalGrade,
    Verdict,
    VerifierName,
)
from ai_backend.graph.state import (
    Claim as ClaimDict,
)
from ai_backend.graph.state import (
    FinalIssue as FinalIssueDict,
)
from ai_backend.graph.state import (
    FinalReport as FinalReportDict,
)
from ai_backend.graph.state import (
    VerificationResult as VerificationResultDict,
)


class CitationModel(BaseModel):
    """Citation model."""

    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(min_length=1)
    citation_type: CitationType

    @classmethod
    def from_typed_dict(cls, citation: CitationDict) -> CitationModel:
        return cls.model_validate(dict(citation))

    def to_typed_dict(self) -> CitationDict:
        return CitationDict(raw_text=self.raw_text, citation_type=self.citation_type)


class ClaimModel(BaseModel):
    """Claim model."""

    model_config = ConfigDict(extra="forbid")

    id: str
    content_hash: str = Field(min_length=12, max_length=12)
    document_id: str

    text: str = Field(min_length=1)
    type: list[ClaimType] = Field(min_length=1)
    context: str
    citations: list[CitationModel] = Field(default_factory=list)

    extracted_at: datetime
    parent_claim_id: str | None = None

    @classmethod
    def from_typed_dict(cls, claim: ClaimDict) -> ClaimModel:
        return cls.model_validate(dict(claim))

    def to_typed_dict(self) -> ClaimDict:
        return ClaimDict(
            id=self.id,
            content_hash=self.content_hash,
            document_id=self.document_id,
            text=self.text,
            type=self.type,
            context=self.context,
            citations=[c.to_typed_dict() for c in self.citations],
            extracted_at=self.extracted_at.isoformat(),
            parent_claim_id=self.parent_claim_id,
        )


class VerificationResultModel(BaseModel):
    """Verifier result model."""

    model_config = ConfigDict(extra="forbid")

    id: str
    claim_id: str
    verifier: VerifierName

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    reasoning: str
    sources: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    verified_at: datetime
    parent_result_id: str | None = None

    @classmethod
    def from_typed_dict(cls, result: VerificationResultDict) -> VerificationResultModel:
        return cls.model_validate(dict(result))

    def to_typed_dict(self) -> VerificationResultDict:
        return VerificationResultDict(
            id=self.id,
            claim_id=self.claim_id,
            verifier=self.verifier,
            verdict=self.verdict,
            confidence=self.confidence,
            evidence=self.evidence,
            reasoning=self.reasoning,
            sources=self.sources,
            metadata=self.metadata,
            verified_at=self.verified_at.isoformat(),
            parent_result_id=self.parent_result_id,
        )


class FinalIssueModel(BaseModel):
    """Final report issue model."""

    model_config = ConfigDict(extra="forbid")

    node: str
    highlighted_text: str
    judgment: Verdict
    problem: str
    suggestion: str = ""

    @classmethod
    def from_typed_dict(cls, issue: FinalIssueDict) -> FinalIssueModel:
        return cls.model_validate(dict(issue))

    def to_typed_dict(self) -> FinalIssueDict:
        return FinalIssueDict(
            node=self.node,
            highlighted_text=self.highlighted_text,
            judgment=self.judgment,
            problem=self.problem,
            suggestion=self.suggestion,
        )


class FinalReportModel(BaseModel):
    """Structured final report model."""

    model_config = ConfigDict(extra="forbid")

    final_grade: FinalGrade
    summary: str
    issues: list[FinalIssueModel] = Field(default_factory=list)

    @classmethod
    def from_typed_dict(cls, report: FinalReportDict) -> FinalReportModel:
        return cls.model_validate(dict(report))

    def to_typed_dict(self) -> FinalReportDict:
        return FinalReportDict(
            final_grade=self.final_grade,
            summary=self.summary,
            issues=[issue.to_typed_dict() for issue in self.issues],
        )
