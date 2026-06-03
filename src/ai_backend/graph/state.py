"""LangGraph State definitions."""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, NotRequired, TypedDict

ClaimType = Literal["FACT", "NUMERIC", "SOURCE", "RECENCY"]
"""Claim classification. A claim can have multiple types."""

VerifierName = Literal["fact", "source", "recency", "numeric"]
"""Verifier node name."""

CitationType = Literal["url", "reference"]
"""Citation kind."""

AnswerType = Literal["Abstractive", "Extractive", "Boolean", "Unanswerable"]
"""AVeriTeC answer type for QA evidence."""

Label = Literal[
    "Supported",
    "Refuted",
    "Not Enough Evidence",
    "Conflicting Evidence/Cherrypicking",
]
"""AVeriTeC veracity label."""

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

    evidence: list[str]
    reasoning: str
    sources: list[str]

    # Internal debugging data. This is useful for developers and logs, but should
    # not be displayed directly to end users.
    metadata: dict[str, Any]

    verified_at: str
    parent_result_id: str | None


class Answer(TypedDict):
    """Single answer for an AVeriTeC-style evidence question."""

    answer: str
    answer_type: AnswerType
    source_url: str
    boolean_explanation: NotRequired[str]  # Boolean일 때 판단 근거; 나머지는 생략


class Question(TypedDict):
    """AVeriTeC-style QA evidence item."""

    question: str
    answers: list[Answer]
    claim_id: NotRequired[str]  # 어떤 claim의 QA인지 추적


class ClaimLabel(TypedDict):
    """Per-claim aggregate label."""

    claim_id: str
    label: Label
    justification: str


class GraphState(TypedDict):
    """State passed through the whole verification graph."""

    raw_text: str
    document_id: str
    averitec_claim_types: NotRequired[list[str]]
    claim_date: NotRequired[str]  # DD-MM-YYYY (AVeriTeC)
    document_citations: list[Citation]

    claims: list[Claim]

    questions: Annotated[list[Question], add]

    fact_results: Annotated[list[VerificationResult], add]
    source_results: Annotated[list[VerificationResult], add]
    recency_results: Annotated[list[VerificationResult], add]
    numeric_results: Annotated[list[VerificationResult], add]

    claim_labels: Annotated[list[ClaimLabel], add]
