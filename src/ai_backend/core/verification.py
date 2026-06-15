"""Shared helpers for verification nodes."""

from __future__ import annotations

import logging
import re as _re
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from ai_backend.core.ids import make_verification_result
from ai_backend.core.search import SearchClient, SearchResult
from ai_backend.core.search_policy import (
    build_search_profile,
    expand_official_queries,
    needs_official_retry,
    rank_search_results,
    search_policy_metadata,
)
from ai_backend.graph.state import Claim, Question, VerificationResult, VerifierName

logger = logging.getLogger(__name__)

_KOREAN_RE = _re.compile(r"[가-힣]")
_TOKEN_RE = _re.compile(r"[A-Za-z0-9가-힣]+")

_QUESTION_STARTERS = frozenset((
    "what", "who", "when", "where", "why", "how",
    "did", "does", "is", "are", "was", "were",
    "has", "have", "had", "can", "could", "would",
))

_QUESTION_GEN_PROMPT = (
    "Given a fact-checking claim and the evidence found, "
    "generate one concise question (in English) that this evidence answers. "
    'The question should be in natural language like "Did X happen?" or "What is Y?".\n\n'
    "Claim: {claim}\n"
    "Evidence summary: {evidence}\n\n"
    "Return only the question, no explanation."
)

_NUMERIC_ANSWER_PROMPT = (
    "From the evidence below, extract ONE concise sentence (under 30 words) "
    "that directly states the key number or statistic relevant to the claim.\n"
    "If no specific number is found, write 'No specific number found in evidence.'\n\n"
    "Claim: {claim}\n"
    "Evidence: {evidence}\n\n"
    "Return only the sentence, no explanation."
)

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


def normalize_judgment(value: Any) -> str:
    """Normalize an LLM judgment to the verifier verdict set."""
    judgment = str(value or "").strip().upper()
    return judgment if judgment in PASSING_JUDGMENTS else "WARNING"


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
    verifier: VerifierName | None = None,
    days: int | None = None,
) -> SearchEvidenceBundle:
    """Search and rerank evidence."""
    profile = build_search_profile(claim, intent=verifier or "generic")
    base_results = search_evidence(
        queries,
        search_client=search_client,
        max_results_per_query=max_results_per_query,
        days=days,
    )
    ranked = rank_search_results(profile, base_results)

    return SearchEvidenceBundle(
        results=[item.result for item in ranked],
        metadata=search_policy_metadata(
            profile,
            expanded_queries=[],
            official_retry=False,
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


def select_answer_source_url(
    answer: str,
    results: list[SearchResult],
    *,
    question: str = "",
    boolean_explanation: str = "",
) -> str:
    """Choose the evidence URL that best matches a generated QA answer."""
    if not results:
        return ""
    if len(results) == 1:
        return results[0].url

    answer_text = " ".join(
        part.strip()
        for part in (answer, boolean_explanation, question)
        if part and part.strip()
    )
    answer_tokens = _tokens(answer_text)
    if not answer_tokens:
        return results[0].url

    best_result = results[0]
    best_score = -1.0
    normalized_answer = _normalize_for_match(answer_text)
    for idx, item in enumerate(results):
        source_text = f"{item.title} {item.content}"
        source_tokens = _tokens(source_text)
        if not source_tokens:
            continue

        overlap = len(answer_tokens & source_tokens)
        precision = overlap / len(answer_tokens)
        recall = overlap / len(source_tokens)
        score = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0

        normalized_source = _normalize_for_match(source_text)
        if len(normalized_answer) >= 24 and normalized_answer in normalized_source:
            score += 1.0
        if item.url:
            score += 0.001 * (len(results) - idx)

        if score > best_score:
            best_score = score
            best_result = item
    return best_result.url


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


def _normalize_for_match(text: str) -> str:
    return " ".join(_TOKEN_RE.findall(text.lower()))


def evidence_summary(item: SearchResult) -> str:
    """Create a compact evidence summary for API output."""
    title = item.title.strip()
    content = item.content.strip()
    if len(content) > 240:
        content = content[:237] + "..."
    return f"{title}: {content}" if title else content


def evidence_answer(item: SearchResult) -> str:
    """Create an AVeriTeC-style answer from one search result."""
    content = item.content.strip()
    if content:
        return content
    title = item.title.strip()
    return title or "No answer could be extracted from the source."


def question_from_evidence(
    question: str,
    results: list[SearchResult],
) -> Question:
    """Build a QA evidence item from ranked search results."""
    answers = [
        {
            "answer": evidence_answer(item),
            "answer_type": "Abstractive",
            "source_url": item.url,
        }
        for item in results
        if item.url and evidence_answer(item)
    ]
    if not answers:
        answers = [
            {
                "answer": "No sufficient evidence was found.",
                "answer_type": "Unanswerable",
                "source_url": "",
            }
        ]
    return Question(question=question, answers=answers)


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
        evidence=[],
        reasoning=reason,
        sources=[],
        metadata={"error": reason},
    )


def lang_instruction(claim: Claim) -> str:
    """Return an English-query instruction appended to prompts for non-Korean claims."""
    text = f"{claim['text']} {claim.get('context', '')}"
    if _KOREAN_RE.search(text):
        return ""
    return "\n\nIMPORTANT: This claim is in English. You MUST generate all search_queries in English."


def rule_based_question(claim: Claim, queries: list[str]) -> str:
    """Convert the first search query into a natural-language question.

    If the query starts with a question word it gets a "?" appended;
    otherwise it is wrapped as "What does the evidence say about ...?".
    Falls back to a generic claim-based question when queries is empty.
    """
    if not queries:
        return f"What evidence verifies this claim: {claim['text']}?"
    q = queries[0].strip().rstrip("?")
    if not q:
        return f"What evidence verifies this claim: {claim['text']}?"
    if q.lower().split()[0] in _QUESTION_STARTERS:
        return q + "?"
    return f"What does the evidence say about {q}?"


def generate_question(
    claim: Claim,
    evidence_results: list[SearchResult],
    queries: list[str],
    *,
    llm: BaseChatModel,
) -> str:
    """LLM-based natural-language question generation with rule-based fallback.

    Used in averitec mode only, so the extra LLM call does not affect service latency.
    """
    evidence_text = " ".join(r.content[:200] for r in evidence_results[:2])
    try:
        response = llm.invoke(
            [HumanMessage(content=_QUESTION_GEN_PROMPT.format(
                claim=claim["text"],
                evidence=evidence_text or "No evidence found.",
            ))]
        )
        result = message_content(response.content).strip().strip('"')
        if result:
            return result
    except Exception:
        logger.warning("generate_question LLM call failed; falling back to rule_based_question")
    return rule_based_question(claim, queries)


def extract_numeric_answer(
    claim: Claim,
    evidence_results: list[SearchResult],
    *,
    llm: BaseChatModel,
) -> str:
    """Extract a single concise numeric sentence from evidence for AVeriTeC NC scoring."""
    evidence_text = " ".join(r.content[:300] for r in evidence_results[:3])
    try:
        response = llm.invoke([HumanMessage(content=_NUMERIC_ANSWER_PROMPT.format(
            claim=claim["text"],
            evidence=evidence_text or "No evidence found.",
        ))])
        result = message_content(response.content).strip()
        if result:
            return result
    except Exception:
        logger.warning("extract_numeric_answer LLM call failed; falling back to evidence content")
    return evidence_results[0].content if evidence_results else "No evidence found."
