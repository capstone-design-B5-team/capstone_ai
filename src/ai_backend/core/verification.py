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

SUPPORT_TYPES = {
    "direct_support",
    "partial_support",
    "contradiction",
    "insufficient_evidence",
    "related_only",
    "unknown",
}
DIRECTNESS_TYPES = {"direct", "indirect", "unknown"}
MISMATCH_TYPES = {
    "none",
    "scope",
    "time",
    "number",
    "attribution",
    "context",
    "methodology",
    "source",
    "unknown",
}

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


def answer_support_metadata(
    parsed: dict[str, Any],
    *,
    claim: str,
    question: str,
    answer: str,
    answer_type: str,
    boolean_explanation: str = "",
    source_url: str = "",
) -> dict[str, str]:
    """Normalize structured evidence-support signals for aggregate."""
    text = " ".join(part for part in (claim, question, answer, boolean_explanation) if part)
    lower = text.lower()

    support_type = _normalize_choice(parsed.get("support_type"), SUPPORT_TYPES, "unknown")
    directness = _normalize_choice(parsed.get("directness"), DIRECTNESS_TYPES, "unknown")
    mismatch_type = _normalize_choice(parsed.get("mismatch_type"), MISMATCH_TYPES, "none")

    if support_type == "unknown":
        support_type = _infer_support_type(answer, answer_type, boolean_explanation, lower)
    if directness == "unknown":
        directness = _infer_directness(lower)
    if mismatch_type == "none":
        mismatch_type = _infer_mismatch_type(lower)

    support_type, directness, mismatch_type = _refine_support_metadata(
        claim=claim,
        question=question,
        answer=answer,
        source_url=source_url,
        support_type=support_type,
        directness=directness,
        mismatch_type=mismatch_type,
        lower=lower,
    )

    return {
        "support_type": support_type,
        "directness": directness,
        "mismatch_type": mismatch_type,
    }


def compact_averitec_answer(
    answer: str,
    *,
    question: str,
    claim: str,
    answer_type: str,
    max_words: int = 70,
) -> str:
    """Keep QA answers short and close to the exact AVeriTeC question."""
    text = " ".join(str(answer or "").split())
    if not text or answer_type == "Unanswerable":
        return text
    if answer_type == "Boolean":
        return text if text in {"Yes", "No"} else text.split(".", 1)[0].strip()
    if _word_count(text) <= max_words:
        return text

    sentences = _split_sentences(text)
    if not sentences:
        return _truncate_words(text, max_words)

    query_tokens = _content_tokens(f"{question} {claim}")
    best = max(
        sentences,
        key=lambda sentence: _sentence_relevance(sentence, query_tokens),
    )
    if _word_count(best) <= max_words:
        return best
    return _truncate_words(best, max_words)


def compact_boolean_explanation(
    explanation: str,
    *,
    question: str,
    claim: str,
    max_words: int = 55,
) -> str:
    """Shorten Boolean explanations because scorer appends them to Yes/No."""
    text = " ".join(str(explanation or "").split())
    if not text:
        return ""
    if _word_count(text) <= max_words:
        return text
    sentences = _split_sentences(text)
    if not sentences:
        return _truncate_words(text, max_words)
    query_tokens = _content_tokens(f"{question} {claim}")
    best = max(sentences, key=lambda sentence: _sentence_relevance(sentence, query_tokens))
    return best if _word_count(best) <= max_words else _truncate_words(best, max_words)


def _normalize_choice(value: Any, allowed: set[str], fallback: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in allowed else fallback


def _infer_support_type(
    answer: str,
    answer_type: str,
    boolean_explanation: str,
    lower: str,
) -> str:
    answer_l = answer.strip().lower()
    if answer_type == "Unanswerable" or answer_l == "unanswerable":
        return "insufficient_evidence"
    insufficient_markers = (
        "not enough evidence",
        "no sufficient evidence",
        "not aware of any reports",
        "inconclusive",
        "cannot be established",
        "could not verify",
        "not officially confirmed",
    )
    if any(marker in lower for marker in insufficient_markers):
        return "insufficient_evidence"
    partial_markers = (
        "partial",
        "partially",
        "caveat",
        "exception",
        "however",
        "but",
        "although",
        "misleading",
        "context",
        "not humanitarian aid",
        "characterized",
        "third-party",
        "broader",
        "narrower",
    )
    if any(marker in lower for marker in partial_markers):
        return "partial_support"
    related_markers = ("related", "broad topic", "does not directly", "not direct")
    if any(marker in lower for marker in related_markers):
        return "related_only"
    if answer_type == "Boolean":
        if answer_l == "yes":
            return "direct_support"
        if answer_l == "no":
            return "contradiction"
        explanation_l = boolean_explanation.lower()
        if explanation_l.startswith("yes"):
            return "direct_support"
        if explanation_l.startswith("no"):
            return "contradiction"
    contradiction_markers = ("false", "incorrect", "did not", "does not", "no evidence that")
    if any(marker in lower for marker in contradiction_markers):
        return "contradiction"
    return "direct_support"


def _infer_directness(lower: str) -> str:
    indirect_markers = (
        "third-party",
        "characterized",
        "suggests",
        "related policy",
        "interpretation",
        "broad topic",
        "does not directly",
        "not direct",
    )
    if any(marker in lower for marker in indirect_markers):
        return "indirect"
    direct_markers = (
        "directly",
        "exact",
        "official",
        "transcript",
        "source states",
        "evidence states",
    )
    if any(marker in lower for marker in direct_markers):
        return "direct"
    return "unknown"


def _infer_mismatch_type(lower: str) -> str:
    if any(marker in lower for marker in ("speaker", "said", "quote", "attribution")):
        if any(marker in lower for marker in ("third-party", "characterized", "not direct")):
            return "attribution"
    if any(marker in lower for marker in ("annualized", "per capita", "methodology", "baseline")):
        return "methodology"
    if any(marker in lower for marker in ("number", "percent", "%", "unit", "denominator")):
        if any(marker in lower for marker in ("different", "mismatch", "not match")):
            return "number"
    if any(marker in lower for marker in ("timeframe", "claim date", "outdated", "latest")):
        return "time"
    if any(marker in lower for marker in ("scope", "geography", "jurisdiction", "population")):
        return "scope"
    if any(marker in lower for marker in ("context", "caveat", "misleading", "exception")):
        return "context"
    return "none"


def _refine_support_metadata(
    *,
    claim: str,
    question: str,
    answer: str,
    source_url: str,
    support_type: str,
    directness: str,
    mismatch_type: str,
    lower: str,
) -> tuple[str, str, str]:
    """Downgrade brittle direct-support signals before aggregation."""
    claim_l = claim.lower()
    question_l = question.lower()
    answer_l = answer.lower()
    numeric_claim = _is_numeric_or_comparative_claim(claim_l)

    if support_type == "direct_support" and _is_context_probe(question_l):
        support_type = "partial_support"
        if mismatch_type == "none":
            mismatch_type = "context"

    if support_type == "direct_support" and "fraud" in claim_l and "no evidence" in answer_l:
        support_type = "contradiction"

    if support_type == "direct_support" and numeric_claim and mismatch_type == "methodology":
        support_type = "partial_support"

    if support_type == "direct_support" and numeric_claim and _has_missing_basis_signal(lower):
        support_type = "insufficient_evidence"
        if mismatch_type == "none":
            mismatch_type = "methodology"

    return support_type, directness, mismatch_type


def _is_context_probe(question: str) -> bool:
    return question.startswith(
        (
            "what factors",
            "what was the context",
            "were there any exceptions",
            "are there any exceptions",
            "what policies",
            "what sources",
            "which sources",
            "what is the source",
        )
    )


def _is_numeric_or_comparative_claim(claim: str) -> bool:
    return bool(
        _has_non_year_number(claim)
        or _re.search(r"%|percent|per cent", claim)
        or any(
            marker in claim
            for marker in (
                "more than",
                "less than",
                "cheaper than",
                "higher than",
                "lower than",
                "three times",
                "per capita",
                "reduced from",
                "drop",
                "increase",
                "decrease",
            )
        )
    )


def _has_non_year_number(text: str) -> bool:
    for match in _re.finditer(r"\b\d+(?:\.\d+)?\b", text):
        value = match.group(0)
        if len(value) == 4 and value.startswith(("19", "20")):
            continue
        return True
    return False


def _has_missing_basis_signal(lower: str) -> bool:
    return any(
        marker in lower
        for marker in (
            "did not make reference",
            "does not make reference",
            "not make reference",
            "unable to confirm",
            "cannot confirm",
            "not released to the public",
            "not been released to the public",
            "no price is offered",
            "no specific price",
            "no official data",
            "not available",
            "could not be verified",
        )
    )


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


def _content_tokens(text: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "did",
        "does",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "there",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "with",
    }
    return {token for token in _tokens(text) if token not in stopwords and len(token) > 1}


def _split_sentences(text: str) -> list[str]:
    parts = _re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part.strip(" \t\r\n-") for part in parts if part.strip(" \t\r\n-")]


def _sentence_relevance(sentence: str, query_tokens: set[str]) -> tuple[int, int, int]:
    sentence_tokens = _content_tokens(sentence)
    overlap = len(sentence_tokens & query_tokens)
    numbers = len(_re.findall(r"\d+(?:\.\d+)?|%|percent|per cent", sentence.lower()))
    return overlap, numbers, -_word_count(sentence)


def _word_count(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(" ,;:") + "."


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
    return (
        "\n\nIMPORTANT: This claim is in English. "
        "You MUST generate all search_queries in English."
    )


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
