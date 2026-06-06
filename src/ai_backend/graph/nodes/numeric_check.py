"""Numeric verification node."""

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
    SearchClient,
    get_search_client,
)
from ai_backend.core.verification import (
    SearchEvidenceBundle,
    evidence_summary,
    first_result,
    format_evidence,
    lang_instruction,
    message_content,
    rule_based_question,
    search_verification_evidence,
)
from ai_backend.core.verification import (
    make_unverifiable_result as build_unverifiable_result,
)
from ai_backend.graph.prompts.numeric_check import (
    NUMERIC_ANSWER_USER,
    NUMERIC_CHECK_SYSTEM,
    NUMERIC_QUESTION_USER,
)
from ai_backend.graph.state import Claim, GraphState, Question, VerificationResult

logger = logging.getLogger(__name__)


def numeric_check_node(
    state: GraphState,
    *,
    llm: BaseChatModel | None = None,
    search_client: SearchClient | None = None,
    max_results_per_query: int = 5,
    max_workers: int = 4,
) -> dict[str, list[VerificationResult] | list[Question]]:
    """Verify NUMERIC claims and return a LangGraph partial update."""
    started = perf_counter()
    numeric_claims = [claim for claim in state["claims"] if "NUMERIC" in claim["type"]]
    logger.info(
        "numeric_check_node started claims=%d numeric_claims=%d",
        len(state["claims"]),
        len(numeric_claims),
    )
    if not numeric_claims:
        logger.info("numeric_check_node skipped no NUMERIC claims")
        return {"numeric_results": []}

    claim_date = state.get("claim_date", "")
    llm = llm if llm is not None else get_llm("verification")

    try:
        search_client = search_client if search_client is not None else get_search_client()
    except Exception as exc:
        logger.exception("numeric_check_node: search client initialization failed")
        return {
            "numeric_results": [
                _make_unverifiable_result(claim, f"검색 클라이언트 초기화 실패: {exc}")
                for claim in numeric_claims
            ],
            "questions": [
                _make_unanswerable_question(claim, f"검색 클라이언트 초기화 실패: {exc}")
                for claim in numeric_claims
            ],
        }

    def verify_one(index: int, claim: Claim) -> tuple[VerificationResult, list[Question]]:
        claim_started = perf_counter()
        logger.info(
            "numeric_check_node claim started %d/%d claim_id=%s text=%r",
            index,
            len(numeric_claims),
            claim["id"],
            claim["text"][:160],
        )
        try:
            result = _verify_numeric_claim(
                claim,
                llm=llm,
                search_client=search_client,
                max_results_per_query=max_results_per_query,
                claim_date=claim_date,
            )
            logger.info(
                "numeric_check_node claim finished %d/%d claim_id=%s elapsed=%.2fs",
                index,
                len(numeric_claims),
                claim["id"],
                perf_counter() - claim_started,
            )
            return result
        except Exception as exc:
            logger.exception("numeric_check_node: claim verification failed (%s)", claim["id"])
            reason = f"수치 검증 실패: {exc}"
            return _make_unverifiable_result(claim, reason), [
                _make_unanswerable_question(claim, reason)
            ]

    results: list[tuple[VerificationResult, list[Question]] | None] = [None] * len(numeric_claims)
    worker_count = max(1, min(max_workers, len(numeric_claims)))
    logger.info("numeric_check_node running claim workers=%d", worker_count)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_index = {
            executor.submit(verify_one, index, claim): index - 1
            for index, claim in enumerate(numeric_claims, start=1)
        }
        for future in as_completed(future_to_index):
            results[future_to_index[future]] = future.result()

    logger.info(
        "numeric_check_node finished elapsed=%.2fs results=%d",
        perf_counter() - started,
        len(numeric_claims),
    )
    completed = [result for result in results if result is not None]
    return {
        "numeric_results": [result for result, _questions in completed],
        "questions": [q for _result, qs in completed for q in qs],
    }


def _verify_numeric_claim(
    claim: Claim,
    *,
    llm: BaseChatModel,
    search_client: SearchClient,
    max_results_per_query: int,
    claim_date: str = "",
) -> tuple[VerificationResult, list[Question]]:
    # Step 1: Multi NL questions + search queries
    q_items = _request_numeric_questions(claim, llm=llm, claim_date=claim_date)
    if not q_items:
        q_items = [{"question": _question_text(claim, [claim["text"]]), "search_queries": [claim["text"]]}]

    # Deduplicate search queries across all questions
    all_queries: list[str] = (list(dict.fromkeys(
        q for qi in q_items for q in qi.get("search_queries", [])
    )) or [claim["text"]])[:3]

    # Step 2: Search evidence
    evidence_bundle = _search_evidence(
        claim,
        all_queries,
        search_client=search_client,
        max_results_per_query=max_results_per_query,
    )
    evidence_results = evidence_bundle.results
    evidence_text = format_evidence(evidence_results)

    result = make_verification_result(
        claim_id=claim["id"],
        verifier="numeric",
        evidence=[evidence_summary(item) for item in evidence_results],
        reasoning=f"search_queries={all_queries}",
        sources=[item.url for item in evidence_results if item.url],
        metadata={
            "search_queries": all_queries,
            **evidence_bundle.metadata,
        },
    )

    # Step 3: Per-question answer generation
    source_url = evidence_results[0].url if evidence_results else ""
    questions: list[Question] = []
    for qi in q_items:
        q_text = qi.get("question", "") or _question_text(claim, all_queries)
        a_result = _request_numeric_answer(
            claim, question=q_text, evidence_text=evidence_text, llm=llm
        )
        answer = a_result.get("answer", "")
        answer_type = a_result.get("answer_type", "Extractive")
        boolean_explanation = a_result.get("boolean_explanation", "")
        if answer:
            answer_dict: dict = {"answer": answer, "answer_type": answer_type, "source_url": source_url}
            if answer_type == "Boolean":
                answer_dict["boolean_explanation"] = boolean_explanation
            questions.append(Question(question=q_text, answers=[answer_dict], claim_id=claim["id"]))

    if not questions:
        return result, [_make_unanswerable_question(claim, "No sufficient evidence was found.")]
    return result, questions


def _request_numeric_questions(claim: Claim, *, llm: BaseChatModel, claim_date: str = "") -> list[dict]:
    response = llm.invoke(
        [
            SystemMessage(content=NUMERIC_CHECK_SYSTEM),
            HumanMessage(
                content=NUMERIC_QUESTION_USER.format(
                    claim=claim["text"],
                    context=claim.get("context", ""),
                    claim_date=claim_date or "(정보 없음)",
                ) + lang_instruction(claim)
            ),
        ]
    )
    parsed = parse_json_with_fallback(message_content(response.content))
    if isinstance(parsed, dict):
        questions = parsed.get("questions")
        if isinstance(questions, list) and questions:
            return questions
        # backward compat: old {"question": "...", "search_queries": [...]} format
        question = parsed.get("question", "")
        search_queries = parsed.get("search_queries", [])
        if question:
            return [{"question": question, "search_queries": search_queries}]
    return []


def _request_numeric_answer(
    claim: Claim,
    *,
    question: str,
    evidence_text: str,
    llm: BaseChatModel,
) -> dict[str, Any]:
    response = llm.invoke(
        [
            SystemMessage(content=NUMERIC_CHECK_SYSTEM),
            HumanMessage(
                content=NUMERIC_ANSWER_USER.format(
                    claim=claim["text"],
                    question=question,
                    evidence=evidence_text or "(검색 증거 없음)",
                )
            ),
        ]
    )
    parsed = parse_json_with_fallback(message_content(response.content))
    return first_result(parsed, marker_keys={"answer", "answer_type"}) or {}


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
        logger.exception("numeric_check_node: search failed")
        return SearchEvidenceBundle(results=[], metadata={})


def _make_unverifiable_result(claim: Claim, reason: str) -> VerificationResult:
    return build_unverifiable_result(claim, verifier="numeric", reason=reason)


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
        claim_id=claim["id"],
    )


def _question_text(claim: Claim, queries: list[str]) -> str:
    return rule_based_question(claim, queries)
