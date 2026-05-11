"""LangGraph State definitions."""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, TypedDict

ClaimType = Literal["FACT", "NUMERIC", "SOURCE", "RECENCY"]
"""Claim classification. A claim can have multiple types."""

Verdict = Literal["PASS", "WARNING", "FAIL", "UNVERIFIABLE"]
"""Verifier judgment, aligned with all node prompts."""

VerifierName = Literal["fact", "source", "recency", "numeric"]
"""Verifier node name."""

CitationType = Literal["url", "reference"]
"""Citation kind."""

FinalGrade = Literal["통과", "주의", "확인 필요"]
"""Final aggregate grade exposed to users."""


class Citation(TypedDict):
    """Citation attached to a document or claim."""

    raw_text: str
    citation_type: CitationType


class Claim(TypedDict):
    """Claim extracted from the source document."""

    id: str
    content_hash: str
    document_id: str

    text: str
    type: list[ClaimType]
    context: str
    citations: list[Citation]

    extracted_at: str
    parent_claim_id: str | None


class VerificationResult(TypedDict):
    """Single verifier result for one claim."""

    id: str
    claim_id: str
    verifier: VerifierName

    verdict: Verdict
    confidence: float
    evidence: list[str]
    reasoning: str
    sources: list[str]

    # Internal debugging data. This is useful for developers and logs, but should
    # not be displayed directly to end users.
    metadata: dict[str, Any]

    verified_at: str
    parent_result_id: str | None


class FinalIssue(TypedDict):
    """Issue item in the final report."""

    node: str
    original_text: str
    judgment: Verdict
    reason: str
    suggestion: str


class FinalReport(TypedDict):
    """Structured final report from aggregate node."""

    final_grade: FinalGrade
    summary: str
    issues: list[FinalIssue]


class GraphState(TypedDict):
    """State passed through the whole verification graph."""

    raw_text: str
    document_id: str
    document_citations: list[Citation]

    claims: list[Claim]

    fact_results: Annotated[list[VerificationResult], add]
    source_results: Annotated[list[VerificationResult], add]
    recency_results: Annotated[list[VerificationResult], add]
    numeric_results: Annotated[list[VerificationResult], add]

    final_grade: FinalGrade
    final_report: FinalReport
