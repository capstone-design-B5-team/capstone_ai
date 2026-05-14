"""Recency verification node."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from ai_backend.core.ids import make_verification_result
from ai_backend.core.llm import get_llm
from ai_backend.core.parsing import parse_json_with_fallback
from ai_backend.core.search import (
    OpenAIWebSearchClient,
    SearchClient,
    SearchResult,
    get_search_client,
)
from ai_backend.core.search_policy import (
    SearchProfile as RecencyProfile,
)
from ai_backend.core.search_policy import (
    build_search_profile,
    fallback_queries,
    rank_search_results,
)
from ai_backend.core.verification import (
    SearchEvidenceBundle,
    evidence_summary,
    extract_queries,
    first_result,
    format_evidence,
    judgment_confidence,
    message_content,
    normalize_judgment,
    search_verification_evidence,
    string_list,
)
from ai_backend.core.verification import (
    make_unverifiable_result as build_unverifiable_result,
)
from ai_backend.graph.prompts.recency_check import (
    RECENCY_CHECK_SYSTEM,
    RECENCY_JUDGMENT_USER,
    RECENCY_QUERY_USER,
)
from ai_backend.graph.state import Claim, GraphState, VerificationResult

logger = logging.getLogger(__name__)


def recency_check_node(
    state: GraphState,
    *,
    llm: BaseChatModel | None = None,
    search_client: SearchClient | None = None,
    max_results_per_query: int = 3,
    recent_days: int = 730,
    max_workers: int = 4,
) -> dict[str, list[VerificationResult]]:
    """Verify RECENCY claims and return a LangGraph partial update."""
    started = perf_counter()
    recency_claims = [claim for claim in state["claims"] if "RECENCY" in claim["type"]]
    logger.info(
        "recency_check_node started claims=%d recency_claims=%d",
        len(state["claims"]),
        len(recency_claims),
    )
    if not recency_claims:
        logger.info("recency_check_node skipped no RECENCY claims")
        return {"recency_results": []}

    llm = llm if llm is not None else get_llm("verification")

    try:
        search_client = search_client if search_client is not None else get_search_client()
    except Exception as exc:
        logger.exception("recency_check_node: search client initialization failed")
        return {
            "recency_results": [
                _make_unverifiable_result(claim, f"검색 클라이언트 초기화 실패: {exc}")
                for claim in recency_claims
            ]
        }

    def verify_one(index: int, claim: Claim) -> VerificationResult:
        claim_started = perf_counter()
        logger.info(
            "recency_check_node claim started %d/%d claim_id=%s text=%r",
            index,
            len(recency_claims),
            claim["id"],
            claim["text"][:160],
        )
        try:
            result = _verify_recency_claim(
                claim,
                llm=llm,
                search_client=search_client,
                max_results_per_query=max_results_per_query,
                recent_days=recent_days,
            )
            logger.info(
                "recency_check_node claim finished %d/%d claim_id=%s elapsed=%.2fs",
                index,
                len(recency_claims),
                claim["id"],
                perf_counter() - claim_started,
            )
            return result
        except Exception as exc:
            logger.exception("recency_check_node: claim verification failed (%s)", claim["id"])
            return _make_unverifiable_result(claim, f"최신성 검증 실패: {exc}")

    results: list[VerificationResult | None] = [None] * len(recency_claims)
    worker_count = max(1, min(max_workers, len(recency_claims)))
    logger.info("recency_check_node running claim workers=%d", worker_count)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_index = {
            executor.submit(verify_one, index, claim): index - 1
            for index, claim in enumerate(recency_claims, start=1)
        }
        for future in as_completed(future_to_index):
            results[future_to_index[future]] = future.result()

    logger.info(
        "recency_check_node finished elapsed=%.2fs results=%d",
        perf_counter() - started,
        len(recency_claims),
    )
    return {"recency_results": [result for result in results if result is not None]}


def _verify_recency_claim(
    claim: Claim,
    *,
    llm: BaseChatModel,
    search_client: SearchClient,
    max_results_per_query: int,
    recent_days: int,
) -> VerificationResult:
    if isinstance(search_client, OpenAIWebSearchClient):
        return _verify_recency_claim_openai_direct(
            claim,
            search_client=search_client,
            recent_days=recent_days,
        )

    profile = build_search_profile(claim)
    plan = _request_recency_plan(claim, llm=llm)
    queries = extract_queries(plan)
    if not queries:
        queries = fallback_queries(claim, profile, latest=True)

    evidence_bundle = _search_evidence(
        claim,
        queries,
        search_client=search_client,
        max_results_per_query=max_results_per_query,
        recent_days=recent_days,
    )
    evidence_results = evidence_bundle.results
    evidence_text = format_evidence(evidence_results)

    judgment = _request_recency_judgment(claim, evidence_text=evidence_text, llm=llm)
    merged = {**plan, **judgment}
    if "search_queries" not in merged or not merged["search_queries"]:
        merged["search_queries"] = queries

    judgment_value = normalize_judgment(merged.get("judgment"))
    verdict = judgment_value
    confidence = judgment_confidence(judgment_value)

    return make_verification_result(
        claim_id=claim["id"],
        verifier="recency",
        verdict=verdict,
        confidence=confidence,
        evidence=[evidence_summary(item) for item in evidence_results],
        reasoning=_format_reasoning(merged),
        sources=[item.url for item in evidence_results if item.url],
        metadata={
            "node_result": merged,
            "search_queries": merged.get("search_queries", queries),
            "raw_judgment": judgment_value,
            "time_indicators": merged.get("time_indicators", []),
            **evidence_bundle.metadata,
            "recency_profile": evidence_bundle.metadata.get("search_profile", {}),
        },
    )


def _verify_recency_claim_openai_direct(
    claim: Claim,
    *,
    search_client: OpenAIWebSearchClient,
    recent_days: int,
) -> VerificationResult:
    verified = search_client.verify_claim_once(
        claim_text=claim["text"],
        context=claim.get("context", ""),
        claim_types=list(claim["type"]),
        recent_days=recent_days,
    )
    recency_result = verified.get("recency")
    result: dict[str, Any] = recency_result if isinstance(recency_result, dict) else {}
    judgment_value = normalize_judgment(result.get("judgment"))
    evidence = string_list(result.get("evidence"))
    sources = string_list(result.get("sources"))
    queries = string_list(result.get("search_queries")) or [f"{claim['text']} 최신"]

    return make_verification_result(
        claim_id=claim["id"],
        verifier="recency",
        verdict=judgment_value,
        confidence=judgment_confidence(judgment_value),
        evidence=evidence,
        reasoning=_format_reasoning(
            {
                "time_indicators": result.get("time_indicators", []),
                "judgment": judgment_value,
                "search_queries": queries,
                "reason": result.get("reason", ""),
            }
        ),
        sources=sources,
        metadata={
            "node_result": result,
            "search_queries": queries,
            "raw_judgment": judgment_value,
            "time_indicators": result.get("time_indicators", []),
            "direct_openai_web_search": True,
        },
    )


def _request_recency_plan(claim: Claim, *, llm: BaseChatModel) -> dict[str, Any]:
    response = llm.invoke(
        [
            SystemMessage(content=RECENCY_CHECK_SYSTEM),
            HumanMessage(
                content=RECENCY_QUERY_USER.format(
                    claim=claim["text"],
                    context=claim.get("context", ""),
                )
            ),
        ]
    )
    parsed = parse_json_with_fallback(message_content(response.content))
    return first_result(parsed, marker_keys={"search_queries", "judgment"}) or {}


def _build_recency_profile(claim: Claim) -> RecencyProfile:
    return build_search_profile(claim)


def _fallback_recency_queries(claim: Claim, profile: RecencyProfile) -> list[str]:
    return fallback_queries(claim, profile, latest=True)


def _rank_recency_evidence(
    profile: RecencyProfile,
    results: list[SearchResult],
) -> list[SearchResult]:
    return [item.result for item in rank_search_results(profile, results)]


def _request_recency_judgment(
    claim: Claim,
    *,
    evidence_text: str,
    llm: BaseChatModel,
) -> dict[str, Any]:
    response = llm.invoke(
        [
            SystemMessage(content=RECENCY_CHECK_SYSTEM),
            HumanMessage(
                content=RECENCY_JUDGMENT_USER.format(
                    claim=claim["text"],
                    context=claim.get("context", ""),
                    evidence=evidence_text or "(검색 증거 없음)",
                )
            ),
        ]
    )
    parsed = parse_json_with_fallback(message_content(response.content))
    return first_result(parsed, marker_keys={"search_queries", "judgment"}) or {}


def _search_evidence(
    claim: Claim,
    queries: list[str],
    *,
    search_client: SearchClient,
    max_results_per_query: int,
    recent_days: int,
) -> SearchEvidenceBundle:
    try:
        return search_verification_evidence(
            claim,
            queries,
            search_client=search_client,
            max_results_per_query=max_results_per_query,
            days=recent_days,
        )
    except Exception:
        logger.exception("recency_check_node: search failed")
        return SearchEvidenceBundle(results=[], metadata={})


def _format_reasoning(result: dict[str, Any]) -> str:
    indicators = result.get("time_indicators")
    if not isinstance(indicators, list):
        indicators = []
    queries = result.get("search_queries") if isinstance(result.get("search_queries"), list) else []
    judgment = normalize_judgment(result.get("judgment"))
    reason = str(result.get("reason") or "").strip()
    suggestion = str(result.get("suggestion") or "").strip()

    parts = [
        f"time_indicators={indicators}",
        f"judgment={judgment}",
        f"search_queries={queries}",
    ]
    if reason:
        parts.append(f"reason={reason}")
    if suggestion:
        parts.append(f"suggestion={suggestion}")
    return "\n".join(parts)


def _make_unverifiable_result(claim: Claim, reason: str) -> VerificationResult:
    return build_unverifiable_result(claim, verifier="recency", reason=reason)

