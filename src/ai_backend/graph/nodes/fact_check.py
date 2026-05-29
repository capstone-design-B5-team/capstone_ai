"""Fact verification node."""

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
    get_search_client,
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
    question_from_evidence,
    search_verification_evidence,
    string_list,
)
from ai_backend.core.verification import (
    make_unverifiable_result as build_unverifiable_result,
)
from ai_backend.graph.prompts.fact_check import (
    FACT_CHECK_SYSTEM,
    FACT_JUDGMENT_USER,
    FACT_QUERY_USER,
)
from ai_backend.graph.state import Claim, GraphState, Question, VerificationResult

logger = logging.getLogger(__name__)


def fact_check_node(
    state: GraphState,
    *,
    llm: BaseChatModel | None = None,
    search_client: SearchClient | None = None,
    max_results_per_query: int = 3,
    max_workers: int = 4,
) -> dict[str, list[VerificationResult] | list[Question]]:
    """Verify FACT claims and return a LangGraph partial update."""
    started = perf_counter()
    fact_claims = [claim for claim in state["claims"] if "FACT" in claim["type"]]
    logger.info(
        "fact_check_node started claims=%d fact_claims=%d",
        len(state["claims"]),
        len(fact_claims),
    )
    include_questions = state.get("run_mode") == "averitec"
    if not fact_claims:
        logger.info("fact_check_node skipped no FACT claims")
        return {"fact_results": []}

    llm = llm if llm is not None else get_llm("verification")

    try:
        search_client = search_client if search_client is not None else get_search_client()
    except Exception as exc:
        logger.exception("fact_check_node: search client initialization failed")
        update: dict[str, list[VerificationResult] | list[Question]] = {
            "fact_results": [
                _make_unverifiable_result(claim, f"검색 클라이언트 초기화 실패: {exc}")
                for claim in fact_claims
            ],
        }
        if include_questions:
            update["questions"] = [
                _make_unanswerable_question(
                    claim,
                    f"검색 클라이언트 초기화 실패: {exc}",
                )
                for claim in fact_claims
            ]
        return update

    def verify_one(index: int, claim: Claim) -> tuple[VerificationResult, Question]:
        claim_started = perf_counter()
        logger.info(
            "fact_check_node claim started %d/%d claim_id=%s text=%r",
            index,
            len(fact_claims),
            claim["id"],
            claim["text"][:160],
        )
        try:
            result = _verify_fact_claim(
                claim,
                llm=llm,
                search_client=search_client,
                max_results_per_query=max_results_per_query,
            )
            logger.info(
                "fact_check_node claim finished %d/%d claim_id=%s elapsed=%.2fs",
                index,
                len(fact_claims),
                claim["id"],
                perf_counter() - claim_started,
            )
            return result
        except Exception as exc:
            logger.exception("fact_check_node: claim verification failed (%s)", claim["id"])
            reason = f"사실관계 검증 실패: {exc}"
            return _make_unverifiable_result(claim, reason), _make_unanswerable_question(
                claim,
                reason,
            )

    results: list[tuple[VerificationResult, Question] | None] = [None] * len(fact_claims)
    worker_count = max(1, min(max_workers, len(fact_claims)))
    logger.info("fact_check_node running claim workers=%d", worker_count)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_index = {
            executor.submit(verify_one, index, claim): index - 1
            for index, claim in enumerate(fact_claims, start=1)
        }
        for future in as_completed(future_to_index):
            results[future_to_index[future]] = future.result()

    logger.info(
        "fact_check_node finished elapsed=%.2fs results=%d",
        perf_counter() - started,
        len(fact_claims),
    )
    completed = [result for result in results if result is not None]
    update = {
        "fact_results": [result for result, _question in completed],
    }
    if include_questions:
        update["questions"] = [question for _result, question in completed]
    return update


def _verify_fact_claim(
    claim: Claim,
    *,
    llm: BaseChatModel,
    search_client: SearchClient,
    max_results_per_query: int,
) -> tuple[VerificationResult, Question]:
    if isinstance(search_client, OpenAIWebSearchClient):
        return _verify_fact_claim_openai_direct(claim, search_client=search_client)

    plan = _request_fact_plan(claim, llm=llm)
    queries = extract_queries(plan)
    if not queries:
        queries = [claim["text"]]

    evidence_bundle = _search_evidence(
        claim,
        queries,
        search_client=search_client,
        max_results_per_query=max_results_per_query,
    )
    evidence_results = evidence_bundle.results
    evidence_text = format_evidence(evidence_results)

    judgment = _request_fact_judgment(claim, evidence_text=evidence_text, llm=llm)
    merged = {**plan, **judgment}
    if "search_queries" not in merged or not merged["search_queries"]:
        merged["search_queries"] = queries

    judgment_value = normalize_judgment(merged.get("judgment"))
    verdict = judgment_value
    confidence = judgment_confidence(judgment_value)

    result = make_verification_result(
        claim_id=claim["id"],
        verifier="fact",
        verdict=verdict,
        confidence=confidence,
        evidence=[evidence_summary(item) for item in evidence_results],
        reasoning=_format_reasoning(merged),
        sources=[item.url for item in evidence_results if item.url],
        metadata={
            "node_result": merged,
            "search_queries": merged.get("search_queries", queries),
            "raw_judgment": judgment_value,
            **evidence_bundle.metadata,
        },
    )
    return result, question_from_evidence(_question_text(claim, queries), evidence_results)


def _verify_fact_claim_openai_direct(
    claim: Claim,
    *,
    search_client: OpenAIWebSearchClient,
) -> tuple[VerificationResult, Question]:
    verified = search_client.verify_claim_once(
        claim_text=claim["text"],
        context=claim.get("context", ""),
        claim_types=list(claim["type"]),
    )
    fact_result = verified.get("fact")
    result: dict[str, Any] = fact_result if isinstance(fact_result, dict) else {}
    judgment_value = normalize_judgment(result.get("judgment"))
    evidence = string_list(result.get("evidence"))
    sources = string_list(result.get("sources"))
    queries = string_list(result.get("search_queries")) or [claim["text"]]

    result_obj = make_verification_result(
        claim_id=claim["id"],
        verifier="fact",
        verdict=judgment_value,
        confidence=judgment_confidence(judgment_value),
        evidence=evidence,
        reasoning=_format_reasoning(
            {
                "type": result.get("type", "OpenAIWebSearch"),
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
            "direct_openai_web_search": True,
        },
    )
    return result_obj, _question_from_direct_result(claim, queries, evidence, sources)
def _request_fact_plan(claim: Claim, *, llm: BaseChatModel) -> dict[str, Any]:
    response = llm.invoke(
        [
            SystemMessage(content=FACT_CHECK_SYSTEM),
            HumanMessage(
                content=FACT_QUERY_USER.format(
                    claim=claim["text"],
                    context=claim.get("context", ""),
                )
            ),
        ]
    )
    parsed = parse_json_with_fallback(message_content(response.content))
    return first_result(parsed, marker_keys={"search_queries", "judgment"}) or {}


def _request_fact_judgment(
    claim: Claim,
    *,
    evidence_text: str,
    llm: BaseChatModel,
) -> dict[str, Any]:
    response = llm.invoke(
        [
            SystemMessage(content=FACT_CHECK_SYSTEM),
            HumanMessage(
                content=FACT_JUDGMENT_USER.format(
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
) -> SearchEvidenceBundle:
    try:
        return search_verification_evidence(
            claim,
            queries,
            search_client=search_client,
            max_results_per_query=max_results_per_query,
        )
    except Exception:
        logger.exception("fact_check_node: search failed")
        return SearchEvidenceBundle(results=[], metadata={})


def _format_reasoning(result: dict[str, Any]) -> str:
    fact_type = str(result.get("type") or "Unknown")
    queries = result.get("search_queries") if isinstance(result.get("search_queries"), list) else []
    judgment = normalize_judgment(result.get("judgment"))
    reason = str(result.get("reason") or "").strip()
    suggestion = str(result.get("suggestion") or "").strip()

    parts = [
        f"type={fact_type}",
        f"judgment={judgment}",
        f"search_queries={queries}",
    ]
    if reason:
        parts.append(f"reason={reason}")
    if suggestion:
        parts.append(f"suggestion={suggestion}")
    return "\n".join(parts)


def _make_unverifiable_result(claim: Claim, reason: str) -> VerificationResult:
    return build_unverifiable_result(claim, verifier="fact", reason=reason)


def _make_unanswerable_question(claim: Claim, reason: str) -> Question:
    return Question(
        question=_question_text(claim, []),
        answers=[
            {
                "answer": reason,
                "answer_type": "Unanswerable",
                "source_url": "",
            }
        ],
    )


def _question_text(claim: Claim, queries: list[str]) -> str:
    return queries[0] if queries else f"What evidence verifies this claim: {claim['text']}?"


def _question_from_direct_result(
    claim: Claim,
    queries: list[str],
    evidence: list[str],
    sources: list[str],
) -> Question:
    answers = [
        {
            "answer": item,
            "answer_type": "Abstractive",
            "source_url": sources[index] if index < len(sources) else "",
        }
        for index, item in enumerate(evidence)
        if item
    ]
    if not answers:
        return _make_unanswerable_question(claim, "No sufficient evidence was found.")
    return Question(question=_question_text(claim, queries), answers=answers)

