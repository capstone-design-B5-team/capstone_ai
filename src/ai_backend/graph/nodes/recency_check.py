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
    SearchClient,
    get_search_client,
)
from ai_backend.core.verification import (
    SearchEvidenceBundle,
    answer_support_metadata,
    compact_averitec_answer,
    compact_boolean_explanation,
    evidence_summary,
    first_result,
    format_evidence,
    lang_instruction,
    message_content,
    rule_based_question,
    search_verification_evidence,
    select_answer_source_url,
)
from ai_backend.core.verification import (
    make_unverifiable_result as build_unverifiable_result,
)
from ai_backend.graph.prompts.recency_check import (
    RECENCY_ANSWER_USER,
    RECENCY_CHECK_SYSTEM,
    RECENCY_QUESTION_USER,
)
from ai_backend.graph.state import Claim, GraphState, Question, VerificationResult

logger = logging.getLogger(__name__)


def recency_check_node(
    state: GraphState,
    *,
    llm: BaseChatModel | None = None,
    search_client: SearchClient | None = None,
    max_results_per_query: int = 5,
    recent_days: int = 730,
    max_workers: int = 4,
) -> dict[str, list[VerificationResult] | list[Question]]:
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

    claim_date = state.get("claim_date", "")
    llm = llm if llm is not None else get_llm("verification")

    try:
        search_client = search_client if search_client is not None else get_search_client()
    except Exception as exc:
        logger.exception("recency_check_node: search client initialization failed")
        return {
            "recency_results": [
                _make_unverifiable_result(claim, f"검색 클라이언트 초기화 실패: {exc}")
                for claim in recency_claims
            ],
            "questions": [
                _make_unanswerable_question(claim, f"검색 클라이언트 초기화 실패: {exc}")
                for claim in recency_claims
            ],
        }

    def verify_one(index: int, claim: Claim) -> tuple[VerificationResult, list[Question]]:
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
                claim_date=claim_date,
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
            reason = f"최신성 검증 실패: {exc}"
            return _make_unverifiable_result(claim, reason), [
                _make_unanswerable_question(claim, reason)
            ]

    results: list[tuple[VerificationResult, list[Question]] | None] = [None] * len(recency_claims)
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
    completed = [result for result in results if result is not None]
    return {
        "recency_results": [result for result, _questions in completed],
        "questions": [q for _result, qs in completed for q in qs],
    }


def _verify_recency_claim(
    claim: Claim,
    *,
    llm: BaseChatModel,
    search_client: SearchClient,
    max_results_per_query: int,
    recent_days: int,
    claim_date: str = "",
) -> tuple[VerificationResult, list[Question]]:
    # Step 1: Time indicator extraction + multi NL questions + search queries
    q_data = _request_recency_questions(claim, llm=llm, claim_date=claim_date)
    q_items = q_data.get("questions", [])
    time_indicators = q_data.get("time_indicators", [])
    cherry_pick_direction = q_data.get("cherry_pick_direction", "해당없음")

    if not q_items:
        q_items = [
            {
                "question": _question_text(claim, [claim["text"]]),
                "search_queries": [claim["text"]],
            }
        ]

    # Deduplicate search queries across all questions.
    all_queries: list[str] = (list(dict.fromkeys(
        q for qi in q_items for q in qi.get("search_queries", [])
    )) or [claim["text"]])[:3]

    # Step 2: Search evidence
    evidence_bundle = _search_evidence(
        claim,
        all_queries,
        search_client=search_client,
        max_results_per_query=max_results_per_query,
        recent_days=recent_days,
    )
    evidence_results = evidence_bundle.results
    evidence_text = format_evidence(evidence_results)

    result = make_verification_result(
        claim_id=claim["id"],
        verifier="recency",
        evidence=[evidence_summary(item) for item in evidence_results],
        reasoning=(
            f"time_indicators={time_indicators}\n"
            f"cherry_pick_direction={cherry_pick_direction}\n"
            f"search_queries={all_queries}"
        ),
        sources=[item.url for item in evidence_results if item.url],
        metadata={
            "search_queries": all_queries,
            "time_indicators": time_indicators,
            "cherry_pick_direction": cherry_pick_direction,
            **evidence_bundle.metadata,
        },
    )

    # Step 3: Per-question answer generation
    questions: list[Question] = []
    for qi in q_items:
        q_text = qi.get("question", "") or _question_text(claim, all_queries)
        a_result = _request_recency_answer(
            claim,
            question=q_text,
            evidence_text=evidence_text,
            llm=llm,
        )
        answer = a_result.get("answer", "")
        answer_type = a_result.get("answer_type", "Abstractive")
        boolean_explanation = a_result.get("boolean_explanation", "")
        if answer:
            compact_answer = compact_averitec_answer(
                answer,
                question=q_text,
                claim=claim["text"],
                answer_type=answer_type,
            )
            compact_explanation = compact_boolean_explanation(
                boolean_explanation,
                question=q_text,
                claim=claim["text"],
            )
            source_url = "" if answer_type == "Unanswerable" else select_answer_source_url(
                answer,
                evidence_results,
                question=q_text,
                boolean_explanation=boolean_explanation,
            )
            answer_dict: dict = {
                "answer": compact_answer,
                "answer_type": answer_type,
                "source_url": source_url,
            }
            answer_dict.update(answer_support_metadata(
                a_result,
                claim=claim["text"],
                question=q_text,
                answer=answer,
                answer_type=answer_type,
                boolean_explanation=boolean_explanation,
                source_url=source_url,
            ))
            if answer_type == "Boolean":
                answer_dict["boolean_explanation"] = compact_explanation
            questions.append(Question(question=q_text, answers=[answer_dict], claim_id=claim["id"]))

    if not questions:
        return result, [_make_unanswerable_question(claim, "No sufficient evidence was found.")]
    return result, questions


def _request_recency_questions(
    claim: Claim,
    *,
    llm: BaseChatModel,
    claim_date: str = "",
) -> dict[str, Any]:
    response = llm.invoke(
        [
            SystemMessage(content=RECENCY_CHECK_SYSTEM),
            HumanMessage(
                content=RECENCY_QUESTION_USER.format(
                    claim=claim["text"],
                    context=claim.get("context", ""),
                    claim_date=claim_date or "(정보 없음)",
                ) + lang_instruction(claim)
            ),
        ]
    )
    parsed = parse_json_with_fallback(message_content(response.content))
    if isinstance(parsed, dict):
        return parsed
    return {}


def _request_recency_answer(
    claim: Claim,
    *,
    question: str,
    evidence_text: str,
    llm: BaseChatModel,
) -> dict[str, Any]:
    response = llm.invoke(
        [
            SystemMessage(content=RECENCY_CHECK_SYSTEM),
            HumanMessage(
                content=RECENCY_ANSWER_USER.format(
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
    recent_days: int,
) -> SearchEvidenceBundle:
    try:
        return search_verification_evidence(
            claim,
            queries,
            search_client=search_client,
            max_results_per_query=max_results_per_query,
            verifier="recency",
            days=recent_days,
        )
    except Exception:
        logger.exception("recency_check_node: search failed")
        return SearchEvidenceBundle(results=[], metadata={})


def _make_unverifiable_result(claim: Claim, reason: str) -> VerificationResult:
    return build_unverifiable_result(claim, verifier="recency", reason=reason)


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
