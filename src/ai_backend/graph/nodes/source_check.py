"""Source verification node."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from ai_backend.core.ids import make_verification_result
from ai_backend.core.llm import get_llm
from ai_backend.core.parsing import parse_json_with_fallback
from ai_backend.graph.prompts.source_check import SOURCE_CHECK_SYSTEM, SOURCE_CHECK_USER
from ai_backend.graph.state import Citation, Claim, GraphState, Verdict, VerificationResult

logger = logging.getLogger(__name__)

SourceContextProvider = Callable[[Citation], "SourceContext"]


_DISTORTION_CONFIDENCE: dict[str, float] = {
    "PASS": 0.85,
    "WARNING": 0.55,
    "FAIL": 0.85,
}


@dataclass(frozen=True, slots=True)
class SourceContext:
    """Fetched source access status and extracted context."""

    source: str
    accessibility: str
    context: str
    url: str | None = None


def source_check_node(
    state: GraphState,
    *,
    llm: BaseChatModel | None = None,
    source_context_provider: SourceContextProvider | None = None,
    max_workers: int = 4,
) -> dict[str, list[VerificationResult]]:
    """Verify SOURCE claims and return a LangGraph partial update."""
    started = perf_counter()
    source_claims = [claim for claim in state["claims"] if "SOURCE" in claim["type"]]
    logger.info(
        "source_check_node started claims=%d source_claims=%d",
        len(state["claims"]),
        len(source_claims),
    )
    if not source_claims:
        logger.info("source_check_node skipped no SOURCE claims")
        return {"source_results": []}

    llm = llm if llm is not None else get_llm("verification")
    provider = (
        source_context_provider if source_context_provider is not None else fetch_source_context
    )

    def verify_one(index: int, claim: Claim) -> list[VerificationResult]:
        claim_started = perf_counter()
        logger.info(
            "source_check_node claim started %d/%d claim_id=%s text=%r",
            index,
            len(source_claims),
            claim["id"],
            claim["text"][:160],
        )
        citations = _claim_sources(claim, state)
        logger.info(
            "source_check_node claim citations claim_id=%s citations=%d",
            claim["id"],
            len(citations),
        )
        if not citations:
            return [_make_unverifiable_result(claim, "SOURCE Claim에 검증할 출처가 없습니다.")]

        claim_results: list[VerificationResult] = []
        for citation in citations:
            try:
                fetch_started = perf_counter()
                logger.info(
                    "source_check_node fetch started claim_id=%s source=%r",
                    claim["id"],
                    citation["raw_text"][:160],
                )
                context = provider(citation)
                logger.info(
                    "source_check_node fetch finished claim_id=%s elapsed=%.2fs accessibility=%s",
                    claim["id"],
                    perf_counter() - fetch_started,
                    context.accessibility,
                )
                claim_results.append(_verify_source_claim(claim, context, llm=llm))
            except Exception as exc:
                logger.exception("source_check_node: source verification failed (%s)", claim["id"])
                claim_results.append(_make_unverifiable_result(claim, f"출처 검증 실패: {exc}"))
        logger.info(
            "source_check_node claim finished %d/%d claim_id=%s elapsed=%.2fs",
            index,
            len(source_claims),
            claim["id"],
            perf_counter() - claim_started,
        )
        return claim_results

    ordered_results: list[list[VerificationResult] | None] = [None] * len(source_claims)
    worker_count = max(1, min(max_workers, len(source_claims)))
    logger.info("source_check_node running claim workers=%d", worker_count)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_index = {
            executor.submit(verify_one, index, claim): index - 1
            for index, claim in enumerate(source_claims, start=1)
        }
        for future in as_completed(future_to_index):
            ordered_results[future_to_index[future]] = future.result()

    results = [
        result
        for claim_results in ordered_results
        if claim_results is not None
        for result in claim_results
    ]

    logger.info(
        "source_check_node finished elapsed=%.2fs results=%d",
        perf_counter() - started,
        len(results),
    )
    return {"source_results": results}


def fetch_source_context(citation: Citation) -> SourceContext:
    """Fetch URL citations or pass reference citations through as context."""
    source = citation["raw_text"]
    if citation["citation_type"] == "reference":
        return SourceContext(
            source=source,
            accessibility="REFERENCE",
            context=source,
            url=None,
        )

    try:
        with httpx.Client(follow_redirects=True, timeout=10.0) as client:
            response = client.get(source)
    except httpx.HTTPError as exc:
        return SourceContext(
            source=source,
            accessibility=f"ERROR ({type(exc).__name__})",
            context=str(exc),
            url=source,
        )

    if response.status_code >= 400:
        return SourceContext(
            source=source,
            accessibility=f"ERROR ({response.status_code} {response.reason_phrase})",
            context=f"HTTP {response.status_code} {response.reason_phrase}",
            url=source,
        )

    return SourceContext(
        source=source,
        accessibility=f"OK ({response.status_code})",
        context=_extract_text_preview(response.text),
        url=str(response.url),
    )


def _verify_source_claim(
    claim: Claim,
    source_context: SourceContext,
    *,
    llm: BaseChatModel,
) -> VerificationResult:
    response = llm.invoke(
        [
            SystemMessage(content=SOURCE_CHECK_SYSTEM),
            HumanMessage(
                content=SOURCE_CHECK_USER.format(
                    claim=claim["text"],
                    source=source_context.source,
                    context=source_context.context,
                )
            ),
        ]
    )
    parsed = parse_json_with_fallback(_message_content(response.content))
    result = _first_result(parsed) or {}

    distortion = _normalize_distortion(result.get("distortion_check"))
    verdict = distortion
    confidence = _DISTORTION_CONFIDENCE.get(distortion, 0.25)

    return make_verification_result(
        claim_id=claim["id"],
        verifier="source",
        verdict=verdict,
        confidence=confidence,
        evidence=[_format_evidence(source_context)],
        reasoning=_format_reasoning(result, source_context, distortion),
        sources=[source_context.url or source_context.source],
        metadata={
            "node_result": result,
            "raw_judgment": distortion,
            "accessibility": result.get("accessibility") or source_context.accessibility,
            "source": source_context.source,
        },
    )


def _claim_sources(claim: Claim, state: GraphState) -> list[Citation]:
    citations: list[Citation] = []
    citations.extend(claim.get("citations", []))
    citations.extend(state.get("document_citations", []))

    seen: set[str] = set()
    deduped: list[Citation] = []
    for citation in citations:
        key = f"{citation['citation_type']}:{citation['raw_text']}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(citation)
    return deduped


def _first_result(parsed: Any) -> dict[str, Any] | None:
    if isinstance(parsed, dict):
        results = parsed.get("results")
        if isinstance(results, list) and results and isinstance(results[0], dict):
            return results[0]
        return parsed if "distortion_check" in parsed or "accessibility" in parsed else None
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed[0]
    return None


def _normalize_distortion(value: Any) -> Verdict:
    distortion = str(value or "").strip().upper()
    return cast(Verdict, distortion) if distortion in {"PASS", "WARNING", "FAIL"} else "WARNING"


def _format_evidence(source_context: SourceContext) -> str:
    context = source_context.context.strip()
    if len(context) > 320:
        context = context[:317] + "..."
    return (
        f"source={source_context.source}\n"
        f"accessibility={source_context.accessibility}\n"
        f"context={context}"
    )


def _format_reasoning(
    result: dict[str, Any],
    source_context: SourceContext,
    distortion: str,
) -> str:
    accessibility = str(result.get("accessibility") or source_context.accessibility)
    reason = str(result.get("reason") or "").strip()
    parts = [
        f"source={result.get('source_url') or source_context.source}",
        f"accessibility={accessibility}",
        f"distortion_check={distortion}",
    ]
    if reason:
        parts.append(f"reason={reason}")
    return "\n".join(parts)


def _extract_text_preview(text: str, *, max_chars: int = 4000) -> str:
    compact = " ".join(text.split())
    return compact[:max_chars]


def _message_content(content: Any) -> str:
    return content if isinstance(content, str) else str(content)


def _make_unverifiable_result(claim: Claim, reason: str) -> VerificationResult:
    return make_verification_result(
        claim_id=claim["id"],
        verifier="source",
        verdict="UNVERIFIABLE",
        confidence=0.0,
        evidence=[],
        reasoning=reason,
        sources=[],
        metadata={"error": reason},
    )

