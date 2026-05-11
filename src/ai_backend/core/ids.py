"""ID and object factory utilities."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from ai_backend.graph.state import (
    Citation,
    CitationType,
    Claim,
    ClaimType,
    Verdict,
    VerificationResult,
    VerifierName,
)


def new_id() -> str:
    """Return a UUID4 string."""
    return str(uuid.uuid4())


def now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def normalize_for_hash(text: str) -> str:
    """Normalize text before content hash calculation."""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s가-힣]", "", text)
    return text


def compute_content_hash(text: str) -> str:
    """Return first 12 hex chars of SHA-256 for normalized text."""
    normalized = normalize_for_hash(text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:12]


def make_citation(raw_text: str, citation_type: CitationType) -> Citation:
    """Create a Citation object."""
    return Citation(raw_text=raw_text.strip(), citation_type=citation_type)


def make_claim(
    text: str,
    type_: list[ClaimType],
    context: str,
    document_id: str,
    *,
    citations: list[Citation] | None = None,
    parent_claim_id: str | None = None,
) -> Claim:
    """Create a Claim object with all metadata fields populated."""
    return Claim(
        id=new_id(),
        content_hash=compute_content_hash(text),
        document_id=document_id,
        text=text,
        type=type_,
        context=context,
        citations=citations if citations is not None else [],
        extracted_at=now_iso(),
        parent_claim_id=parent_claim_id,
    )


def make_verification_result(
    claim_id: str,
    verifier: VerifierName,
    verdict: Verdict,
    confidence: float,
    evidence: list[str],
    reasoning: str,
    sources: list[str],
    *,
    metadata: dict[str, Any] | None = None,
    parent_result_id: str | None = None,
) -> VerificationResult:
    """Create a VerificationResult object with all metadata fields populated."""
    return VerificationResult(
        id=new_id(),
        claim_id=claim_id,
        verifier=verifier,
        verdict=verdict,
        confidence=confidence,
        evidence=evidence,
        reasoning=reasoning,
        sources=sources,
        metadata=metadata if metadata is not None else {},
        verified_at=now_iso(),
        parent_result_id=parent_result_id,
    )
