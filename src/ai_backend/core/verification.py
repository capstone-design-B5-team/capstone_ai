"""Shared helpers for verification nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from ai_backend.core.ids import make_verification_result
from ai_backend.core.search import SearchClient, SearchResult
from ai_backend.core.search_policy import (
    build_search_profile,
    expand_official_queries,
    needs_official_retry,
    rank_search_results,
    search_policy_metadata,
)
from ai_backend.graph.state import Claim, Verdict, VerificationResult, VerifierName

PASSING_JUDGMENTS = {"PASS", "WARNING", "FAIL"}
ALL_VERDICTS = {"PASS", "WARNING", "FAIL", "UNVERIFIABLE"}

JUDGMENT_CONFIDENCE: dict[str, float] = {
    "PASS": 0.85,
    "WARNING": 0.55,
    "FAIL": 0.85,
}


@dataclass(frozen=True, slots=True)
class SearchEvidenceBundle:
    """Search evidence plus debug metadata for verifier nodes."""

    results: list[SearchResult]
    metadata: dict[str, object]


def normalize_judgment(value: Any) -> Verdict:
    """Normalize an LLM judgment to the verifier verdict set."""
    judgment = str(value or "").strip().upper()
    return cast(Verdict, judgment) if judgment in PASSING_JUDGMENTS else "WARNING"


def normalize_issue_judgment(value: Any) -> Verdict:
    """Normalize a final report issue judgment."""
    judgment = str(value or "").strip().upper()
    return cast(Verdict, judgment) if judgment in ALL_VERDICTS else "WARNING"


def judgment_confidence(judgment: str) -> float:
    """Return the default confidence for a normalized judgment."""
    return JUDGMENT_CONFIDENCE.get(judgment, 0.25)


def message_content(content: Any) -> str:
    """Return LangChain message content as text."""
    return content if isinstance(content, str) else str(content)


def first_result(parsed: Any, *, marker_keys: set[str]) -> dict[str, Any] | None:
    """Extract the first useful result object from a parsed LLM response."""
    if isinstance(parsed, dict):
        results = parsed.get("results")
        if isinstance(results, list) and results and isinstance(results[0], dict):
            return results[0]
        return parsed if marker_keys.intersection(parsed) else None
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed[0]
    return None


def string_list(value: Any) -> list[str]:
    """Convert a list-like LLM field into a stripped string list."""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def extract_queries(plan: dict[str, Any]) -> list[str]:
    """Extract search queries from a verifier plan."""
    return string_list(plan.get("search_queries"))


def search_evidence(
    queries: list[str],
    *,
    search_client: SearchClient,
    max_results_per_query: int,
    days: int | None = None,
) -> list[SearchResult]:
    """Run search queries and de-duplicate evidence by URL or content."""
    seen_keys: set[str] = set()
    evidence: list[SearchResult] = []
    for query in queries:
        if not query.strip():
            continue
        search_results = search_client.search(
            query,
            max_results=max_results_per_query,
            days=days,
        )
        for item in search_results:
            dedupe_key = item.url or f"{item.title}:{item.content}"
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            evidence.append(item)
    return evidence


def search_verification_evidence(
    claim: Claim,
    queries: list[str],
    *,
    search_client: SearchClient,
    max_results_per_query: int,
    days: int | None = None,
    prefer_official: bool = True,
) -> SearchEvidenceBundle:
    """Search, optionally retry official-domain queries, then rerank evidence."""
    profile = build_search_profile(claim)
    base_results = search_evidence(
        queries,
        search_client=search_client,
        max_results_per_query=max_results_per_query,
        days=days,
    )
    ranked = rank_search_results(profile, base_results)
    official_retry = False
    expanded_queries: list[str] = []

    if prefer_official and needs_official_retry(profile, ranked):
        expanded_queries = expand_official_queries(queries, profile, claim["text"])
        if expanded_queries:
            official_retry = True
            official_results = search_evidence(
                expanded_queries,
                search_client=search_client,
                max_results_per_query=max_results_per_query,
                days=days,
            )
            ranked = rank_search_results(
                profile,
                _dedupe_search_results([*base_results, *official_results]),
            )

    return SearchEvidenceBundle(
        results=[item.result for item in ranked],
        metadata=search_policy_metadata(
            profile,
            expanded_queries=expanded_queries,
            official_retry=official_retry,
            ranked=ranked,
        ),
    )


def _dedupe_search_results(results: list[SearchResult]) -> list[SearchResult]:
    deduped: list[SearchResult] = []
    seen: set[str] = set()
    for item in results:
        key = item.url or f"{item.title}:{item.content}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def format_evidence(results: list[SearchResult]) -> str:
    """Format search results for an LLM judgment prompt."""
    if not results:
        return ""
    lines: list[str] = []
    for idx, item in enumerate(results, start=1):
        published = f" | published={item.published_date}" if item.published_date else ""
        lines.append(
            f"[{idx}] title={item.title}\n"
            f"url={item.url}{published}\n"
            f"snippet={item.content}"
        )
    return "\n\n".join(lines)


def evidence_summary(item: SearchResult) -> str:
    """Create a compact evidence summary for API output."""
    title = item.title.strip()
    content = item.content.strip()
    if len(content) > 240:
        content = content[:237] + "..."
    return f"{title}: {content}" if title else content


def make_unverifiable_result(
    claim: Claim,
    *,
    verifier: VerifierName,
    reason: str,
) -> VerificationResult:
    """Create a standardized UNVERIFIABLE verifier result."""
    return make_verification_result(
        claim_id=claim["id"],
        verifier=verifier,
        verdict="UNVERIFIABLE",
        confidence=0.0,
        evidence=[],
        reasoning=reason,
        sources=[],
        metadata={"error": reason},
    )
