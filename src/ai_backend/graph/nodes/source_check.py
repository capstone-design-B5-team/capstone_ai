"""Source verification node."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal, cast
from urllib.parse import urlparse

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from ai_backend.core.ids import make_verification_result
from ai_backend.core.llm import get_llm
from ai_backend.core.parsing import parse_json_with_fallback
from ai_backend.core.search import SearchClient, get_search_client
from ai_backend.graph.prompts.source_check import SOURCE_CHECK_SYSTEM, SOURCE_CHECK_USER
from ai_backend.graph.state import (
    Citation,
    Claim,
    GraphState,
    Question,
    Verdict,
    VerificationResult,
)

logger = logging.getLogger(__name__)

SourceContextProvider = Callable[[Citation], "SourceContext"]

# averitec QV: LLM 기반 question 생성 프롬프트 (Plan 1)
_SOURCE_QUESTION_PROMPT = (
    "Given a fact-checking claim, generate one concise natural-language question "
    "(in English) that a fact-checker would ask to verify this claim.\n"
    'Example: "Did X say Y?" or "Did institution Z announce policy W?"\n\n'
    "Claim: {claim}\n\n"
    "Return only the question, no explanation."
)


_DISTORTION_CONFIDENCE: dict[str, float] = {
    "PASS": 0.85,
    "WARNING": 0.55,
    "FAIL": 0.85,
}

# ---------------------------------------------------------------------------
# Source credibility
# ---------------------------------------------------------------------------

TrustLevel = Literal["HIGH", "MEDIUM", "UNKNOWN", "LOW"]

# Whitelisted domains — credibility confirmed without calling Tavily.
_TRUSTED_DOMAINS: tuple[str, ...] = (
    # Korean government & public sector
    ".go.kr",
    ".ac.kr",
    ".re.kr",
    "korea.kr",
    "kosis.kr",
    "bok.or.kr",
    # International government
    ".gov",
    # Academic
    ".edu",
    # Major international organizations
    "un.org",
    "who.int",
    "ilo.org",
    "worldbank.org",
    "oecd.org",
    "imf.org",
    "unicef.org",
    "wto.org",
    "iea.org",
)

# How much a trust level scales the base distortion confidence.
_TRUST_CONFIDENCE_FACTOR: dict[str, float] = {
    "HIGH": 1.0,
    "MEDIUM": 0.9,
    "UNKNOWN": 0.75,
    "LOW": 0.5,
}


@dataclass(frozen=True, slots=True)
class CredibilityResult:
    """Domain-level trust judgment for a citation URL."""

    trust_level: TrustLevel
    reason: str
    is_whitelisted: bool


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
    search_client: SearchClient | None = None,
    max_workers: int = 4,
) -> dict[str, list[VerificationResult] | list[Question]]:
    """Verify SOURCE claims and return a LangGraph partial update."""
    started = perf_counter()
    source_claims = [claim for claim in state["claims"] if "SOURCE" in claim["type"]]
    logger.info(
        "source_check_node started claims=%d source_claims=%d",
        len(state["claims"]),
        len(source_claims),
    )
    include_questions = state.get("run_mode") == "averitec"
    if not source_claims:
        logger.info("source_check_node skipped no SOURCE claims")
        return {"source_results": []}

    llm = llm if llm is not None else get_llm("verification")
    provider = (
        source_context_provider if source_context_provider is not None else fetch_source_context
    )

    def verify_one(index: int, claim: Claim) -> tuple[list[VerificationResult], list[Question]]:
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
            if state.get("run_mode") == "averitec":
                # PS/QV claim: 명시적 출처 없이 들어오는 것이 정상이므로
                # Tavily로 원문 출처를 검색한 뒤 기존 검증 흐름에 합류
                context = _search_and_build_context(claim, search_client)
                credibility = CredibilityResult(
                    trust_level="UNKNOWN",
                    reason="출처 명시 없이 검색으로 수집한 출처입니다.",
                    is_whitelisted=False,
                )
                result = _verify_source_claim(claim, context, credibility=credibility, llm=llm)
                # averitec QV: LLM 기반 question 생성 (Plan 1)
                q_text = _generate_source_question(claim, llm=llm)
                questions = [_question_from_source_context(claim, context, question_text=q_text)]
                # averitec QV: 출처 위치 질문 추가 (gold의 "Where was this published?" 패턴 대응)
                provenance_q = _source_provenance_question(context)
                if provenance_q:
                    questions.append(provenance_q)
                return [result], questions
            reason = "SOURCE Claim에 검증할 출처가 없습니다."
            return [_make_unverifiable_result(claim, reason)], [
                _make_unanswerable_question(claim, reason)
            ]

        # averitec: claim당 한 번만 LLM 호출해 question text 생성 (Plan 1)
        source_q_text = _generate_source_question(claim, llm=llm) if include_questions else None

        claim_results: list[VerificationResult] = []
        claim_questions: list[Question] = []
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
                credibility = (
                    check_source_credibility(
                        citation["raw_text"], search_client=search_client
                    )
                    if citation["citation_type"] == "url"
                    else CredibilityResult(
                        trust_level="UNKNOWN",
                        reason="참고문헌 출처는 도메인 신뢰도 확인을 지원하지 않습니다.",
                        is_whitelisted=False,
                    )
                )
                logger.info(
                    "source_check_node credibility claim_id=%s trust=%s whitelisted=%s",
                    claim["id"],
                    credibility.trust_level,
                    credibility.is_whitelisted,
                )
                claim_results.append(
                    _verify_source_claim(claim, context, credibility=credibility, llm=llm)
                )
                claim_questions.append(_question_from_source_context(claim, context, question_text=source_q_text))
            except Exception as exc:
                logger.exception("source_check_node: source verification failed (%s)", claim["id"])
                reason = f"출처 검증 실패: {exc}"
                claim_results.append(_make_unverifiable_result(claim, reason))
                claim_questions.append(_make_unanswerable_question(claim, reason))
        logger.info(
            "source_check_node claim finished %d/%d claim_id=%s elapsed=%.2fs",
            index,
            len(source_claims),
            claim["id"],
            perf_counter() - claim_started,
        )
        return claim_results, claim_questions

    ordered_results: list[tuple[list[VerificationResult], list[Question]] | None] = [
        None
    ] * len(source_claims)
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
        for claim_output in ordered_results
        if claim_output is not None
        for result in claim_output[0]
    ]
    questions = [
        question
        for claim_output in ordered_results
        if claim_output is not None
        for question in claim_output[1]
    ]

    logger.info(
        "source_check_node finished elapsed=%.2fs results=%d",
        perf_counter() - started,
        len(results),
    )
    update: dict[str, list[VerificationResult] | list[Question]] = {"source_results": results}
    if include_questions:
        update["questions"] = questions
    return update


_JS_REDIRECT_MAX_WORDS = 30
_JS_REDIRECT_SHELL_MAX_WORDS = 120

_JS_REDIRECT_PATTERN = re.compile(
    r"(?:window|document|self)\.location(?:\.(?:href|replace|assign))?\s*[=(]"
    r"|location\.(?:replace|assign)\s*\(",
    re.IGNORECASE,
)


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
        with httpx.Client(
            follow_redirects=True,
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; capstone-ai/1.0; +https://github.com/capstone)"},
        ) as client:
            response = client.get(source)

            if response.status_code >= 400:
                return SourceContext(
                    source=source,
                    accessibility=f"ERROR ({response.status_code} {response.reason_phrase})",
                    context=f"HTTP {response.status_code} {response.reason_phrase}",
                    url=source,
                )

            if _is_js_redirect_shell(response.text):
                return SourceContext(
                    source=source,
                    accessibility="JS_REDIRECT",
                    context="해당 사이트는 자동 내용 추출이 어려운 구조입니다.",
                    url=str(response.url),
                )

            return SourceContext(
                source=source,
                accessibility=f"OK ({response.status_code})",
                context=_extract_text_preview(response.text),
                url=str(response.url),
            )
    except httpx.HTTPError as exc:
        return SourceContext(
            source=source,
            accessibility=f"ERROR ({type(exc).__name__})",
            context=str(exc),
            url=source,
        )


def _is_js_redirect_shell(html: str) -> bool:
    """Return True when the page is a JS-only redirect shell with no real content.

    Two detection paths:
    1. Word count: strips <script>, <style>, <noscript> blocks (noscript is fallback
       text, not real content), then counts visible words. Pages below
       _JS_REDIRECT_MAX_WORDS are empty shells.
    2. Pattern + sparse content: if the raw HTML contains explicit JS redirect
       patterns (window.location, location.href, etc.) and visible word count is
       still below _JS_REDIRECT_SHELL_MAX_WORDS, treat as a redirect shell even
       if the site added boilerplate text that pushed past the lower threshold.
    """
    no_code = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    no_code = re.sub(r"<style[^>]*>.*?</style>", " ", no_code, flags=re.DOTALL | re.IGNORECASE)
    no_code = re.sub(r"<noscript[^>]*>.*?</noscript>", " ", no_code, flags=re.DOTALL | re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", " ", no_code)
    word_count = len(plain.split())
    if word_count < _JS_REDIRECT_MAX_WORDS:
        return True
    return bool(_JS_REDIRECT_PATTERN.search(html)) and word_count < _JS_REDIRECT_SHELL_MAX_WORDS


def check_source_credibility(
    url: str,
    *,
    search_client: SearchClient | None = None,
) -> CredibilityResult:
    """Check domain-level credibility: whitelist first, Tavily for the rest.

    Whitelisted domains (government, academic, major intl orgs) return HIGH
    immediately without any API call.  For all other domains Tavily is queried
    once (max_results=3) to confirm the site is a recognised source.
    """
    domain = urlparse(url.lower()).netloc
    if _is_trusted_domain(domain):
        return CredibilityResult(
            trust_level="HIGH",
            reason=f"공식 기관 도메인({domain})으로 높은 신뢰도가 확인됩니다.",
            is_whitelisted=True,
        )

    try:
        client = search_client or get_search_client()
        results = client.search(f"{domain} 공식 기관 신뢰", max_results=3)
        if results and max((r.score for r in results), default=0.0) >= 0.5:
            return CredibilityResult(
                trust_level="MEDIUM",
                reason=f"검색 결과에서 {domain}이 신뢰할 수 있는 출처로 확인됩니다. ({results[0].title})",
                is_whitelisted=False,
            )
        return CredibilityResult(
            trust_level="UNKNOWN",
            reason=f"{domain}에 대한 신뢰도 정보를 충분히 확인하기 어렵습니다.",
            is_whitelisted=False,
        )
    except Exception as exc:
        logger.warning("credibility check failed url=%s: %s", url, exc)
        return CredibilityResult(
            trust_level="UNKNOWN",
            reason="신뢰도 확인 중 오류가 발생했습니다.",
            is_whitelisted=False,
        )


def _is_trusted_domain(domain: str) -> bool:
    return any(
        domain == pattern.lstrip(".")
        or domain.endswith(pattern)
        or domain.endswith(f".{pattern.lstrip('.')}")
        for pattern in _TRUSTED_DOMAINS
    )


def _verify_source_claim(
    claim: Claim,
    source_context: SourceContext,
    *,
    credibility: CredibilityResult,
    llm: BaseChatModel,
) -> VerificationResult:
    # JS_REDIRECT: URL is valid but dynamic JS prevents content extraction.
    # Bypass LLM to avoid false FAIL — the site exists, content is unverifiable.
    if source_context.accessibility == "JS_REDIRECT":
        result = {
            "reason": "신뢰할 수 있는 공식 기관의 출처이나, 사이트 특성상 내용을 자동으로 확인하기 어렵습니다.",
            "suggestion": "출처에 직접 접속하여 해당 내용이 실제로 존재하는지 확인해 주세요.",
        }
        return make_verification_result(
            claim_id=claim["id"],
            verifier="source",
            verdict="WARNING",
            confidence=0.4,
            evidence=[_format_evidence(source_context), _format_credibility(credibility)],
            reasoning=_format_reasoning(result, source_context, "WARNING"),
            sources=[source_context.url or source_context.source],
            metadata={
                "node_result": result,
                "raw_judgment": "WARNING",
                "accessibility": "JS_REDIRECT",
                "source": source_context.source,
                "credibility": _credibility_metadata(credibility),
            },
        )

    response = llm.invoke(
        [
            SystemMessage(content=SOURCE_CHECK_SYSTEM),
            HumanMessage(
                content=SOURCE_CHECK_USER.format(
                    claim=claim["text"],
                    source=source_context.source,
                    context=source_context.context,
                    trust_level=credibility.trust_level,
                    trust_reason=credibility.reason,
                )
            ),
        ]
    )
    parsed = parse_json_with_fallback(_message_content(response.content))
    result = _first_result(parsed) or {}

    distortion = _normalize_distortion(result.get("distortion_check"))

    # PASS from an UNKNOWN-trust source is downgraded to WARNING:
    # content may match but the source itself hasn't been confirmed credible.
    verdict = cast(
        Verdict,
        "WARNING"
        if distortion == "PASS" and credibility.trust_level in {"UNKNOWN", "LOW"}
        else distortion,
    )
    base_confidence = _DISTORTION_CONFIDENCE.get(distortion, 0.25)
    confidence = round(base_confidence * _TRUST_CONFIDENCE_FACTOR[credibility.trust_level], 4)

    return make_verification_result(
        claim_id=claim["id"],
        verifier="source",
        verdict=verdict,
        confidence=confidence,
        evidence=[_format_evidence(source_context), _format_credibility(credibility)],
        reasoning=_format_reasoning(result, source_context, distortion),
        sources=[source_context.url or source_context.source],
        metadata={
            "node_result": result,
            "raw_judgment": distortion,
            "accessibility": result.get("accessibility") or source_context.accessibility,
            "source": source_context.source,
            "credibility": _credibility_metadata(credibility),
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
    suggestion = str(result.get("suggestion") or "").strip()
    parts = [
        f"source={result.get('source_url') or source_context.source}",
        f"accessibility={accessibility}",
        f"distortion_check={distortion}",
    ]
    if reason:
        parts.append(f"reason={reason}")
    if suggestion:
        parts.append(f"suggestion={suggestion}")
    return "\n".join(parts)


def _format_credibility(credibility: CredibilityResult) -> str:
    whitelisted = "화이트리스트" if credibility.is_whitelisted else "Tavily 검색"
    return (
        f"credibility_trust={credibility.trust_level} "
        f"({whitelisted})\n"
        f"credibility_reason={credibility.reason}"
    )


def _credibility_metadata(credibility: CredibilityResult) -> dict[str, Any]:
    return {
        "trust_level": credibility.trust_level,
        "reason": credibility.reason,
        "is_whitelisted": credibility.is_whitelisted,
    }


def _extract_text_preview(text: str, *, max_chars: int = 4000) -> str:
    compact = " ".join(text.split())
    return compact[:max_chars]


def _message_content(content: Any) -> str:
    return content if isinstance(content, str) else str(content)


# averitec QV: 인용구 추출 정규식 (Plan 2)
_QUOTE_RE = re.compile(
    r'["“”\'‘’]([\w\s,.!?]{10,})["“”\'‘’]'
)


def _extract_quote_query(claim_text: str) -> str | None:
    """QV claim 텍스트에서 인용구를 추출해 검색 쿼리로 반환. 없으면 None. (Plan 2)"""
    match = _QUOTE_RE.search(claim_text)
    return match.group(1).strip() if match else None


def _search_and_build_context(
    claim: Claim,
    search_client: SearchClient | None,
) -> SourceContext:
    """출처가 명시되지 않은 PS/QV claim을 위해 Tavily로 원문 출처를 검색한다."""
    client = search_client or get_search_client()
    # averitec QV: 인용구가 있으면 인용구만 추출해 검색 (Plan 2)
    quote = _extract_quote_query(claim["text"])
    query = quote if quote else claim["text"]
    results = client.search(query, max_results=3)
    if not results:
        return SourceContext(
            source=claim["text"],
            accessibility="NO_RESULTS",
            context="검색 결과를 찾을 수 없습니다.",
        )
    top = results[0]
    combined = "\n".join(f"[{r.title}] {r.content}" for r in results)
    return SourceContext(
        source=top.url,
        accessibility=f"SEARCH_OK ({len(results)} results)",
        context=_extract_text_preview(combined),
        url=top.url,
    )


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


def _make_unanswerable_question(claim: Claim, reason: str) -> Question:
    return Question(
        question=_question_text(claim),
        answers=[
            {
                "answer": reason,
                "answer_type": "Unanswerable",
                "source_url": "",
            }
        ],
    )


def _question_from_source_context(
    claim: Claim,
    source_context: SourceContext,
    *,
    question_text: str | None = None,
) -> Question:
    context = source_context.context.strip()
    if not context or source_context.accessibility.startswith("ERROR"):
        return _make_unanswerable_question(claim, context or source_context.accessibility)
    q = question_text if question_text is not None else _question_text(claim)
    return Question(
        question=q,
        answers=[
            {
                "answer": context,
                "answer_type": "Extractive",
                "source_url": source_context.url or source_context.source,
            }
        ],
    )


def _question_text(claim: Claim) -> str:
    return f"Does the cited source support this claim: {claim['text']}?"


def _source_provenance_question(source_context: SourceContext) -> Question | None:
    """averitec QV/PS용 출처 위치 질문. 추가 Tavily 호출 없이 기존 context에서 생성."""
    url = source_context.url or source_context.source
    if not url or source_context.accessibility.startswith("ERROR"):
        return None
    domain = urlparse(url.lower()).netloc or url
    return Question(
        question="Where was this claim originally published or found?",
        answers=[{
            "answer": domain,
            "answer_type": "Extractive",
            "source_url": url,
        }],
    )


def _generate_source_question(claim: Claim, *, llm: BaseChatModel) -> str:
    """averitec QV용 LLM 기반 question 생성. 실패 시 고정 문자열로 fallback. (Plan 1)"""
    try:
        response = llm.invoke([HumanMessage(content=_SOURCE_QUESTION_PROMPT.format(
            claim=claim["text"],
        ))])
        result = _message_content(response.content).strip().strip('"')
        if result:
            return result
    except Exception:
        pass
    return _question_text(claim)

