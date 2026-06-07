"""Pydantic models equivalent to graph state TypedDicts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_backend.graph.state import (
    Answer as AnswerDict,
)
from ai_backend.graph.state import (
    AnswerType,
    CitationType,
    ClaimType,
    Label,
    VerifierName,
)
from ai_backend.graph.state import (
    Citation as CitationDict,
)
from ai_backend.graph.state import (
    Claim as ClaimDict,
)
from ai_backend.graph.state import (
    ClaimLabel as ClaimLabelDict,
)
from ai_backend.graph.state import (
    Question as QuestionDict,
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
            evidence=self.evidence,
            reasoning=self.reasoning,
            sources=self.sources,
            metadata=self.metadata,
            verified_at=self.verified_at.isoformat(),
            parent_result_id=self.parent_result_id,
        )


class AnswerModel(BaseModel):
    """AVeriTeC-style answer model."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    answer_type: AnswerType
    source_url: str = ""
    boolean_explanation: str = ""
    support_type: str = ""
    directness: str = ""
    mismatch_type: str = ""

    @classmethod
    def from_typed_dict(cls, answer: AnswerDict) -> AnswerModel:
        return cls.model_validate(dict(answer))

    def to_typed_dict(self) -> AnswerDict:
        result = AnswerDict(
            answer=self.answer,
            answer_type=self.answer_type,
            source_url=self.source_url,
        )
        if self.boolean_explanation:
            result["boolean_explanation"] = self.boolean_explanation
        if self.support_type:
            result["support_type"] = self.support_type
        if self.directness:
            result["directness"] = self.directness
        if self.mismatch_type:
            result["mismatch_type"] = self.mismatch_type
        return result


class QuestionModel(BaseModel):
    """AVeriTeC-style QA evidence model."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    answers: list[AnswerModel] = Field(default_factory=list)
    claim_id: str = ""

    @classmethod
    def from_typed_dict(cls, question: QuestionDict) -> QuestionModel:
        return cls.model_validate(dict(question))

    def to_typed_dict(self) -> QuestionDict:
        result = QuestionDict(
            question=self.question,
            answers=[answer.to_typed_dict() for answer in self.answers],
        )
        if self.claim_id:
            result["claim_id"] = self.claim_id
        return result


class ClaimLabelModel(BaseModel):
    """Per-claim aggregate label model."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    label: Label
    justification: str

    @classmethod
    def from_typed_dict(cls, claim_label: ClaimLabelDict) -> ClaimLabelModel:
        return cls.model_validate(dict(claim_label))


