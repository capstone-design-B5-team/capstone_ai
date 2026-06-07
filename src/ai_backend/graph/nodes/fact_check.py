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
from ai_backend.graph.prompts.fact_check import (
    FACT_ANSWER_USER,
    FACT_CHECK_SYSTEM,
    FACT_QUESTION_USER,
)
from ai_backend.graph.state import Claim, GraphState, Question, VerificationResult

logger = logging.getLogger(__name__)


def fact_check_node(
    state: GraphState,
    *,
    llm: BaseChatModel | None = None,
    search_client: SearchClient | None = None,
    max_results_per_query: int = 5,
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
    if not fact_claims:
        logger.info("fact_check_node skipped no FACT claims")
        return {"fact_results": []}

    claim_date = state.get("claim_date", "")
    llm = llm if llm is not None else get_llm("verification")

    try:
        search_client = search_client if search_client is not None else get_search_client()
    except Exception as exc:
        logger.exception("fact_check_node: search client initialization failed")
        return {
            "fact_results": [
                _make_unverifiable_result(claim, f"검색 클라이언트 초기화 실패: {exc}")
                for claim in fact_claims
            ],
            "questions": [
                _make_unanswerable_question(claim, f"검색 클라이언트 초기화 실패: {exc}")
                for claim in fact_claims
            ],
        }

    def verify_one(index: int, claim: Claim) -> tuple[VerificationResult, list[Question]]:
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
                claim_date=claim_date,
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
            return _make_unverifiable_result(claim, reason), [
                _make_unanswerable_question(claim, reason)
            ]

    results: list[tuple[VerificationResult, list[Question]] | None] = [None] * len(fact_claims)
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
    return {
        "fact_results": [result for result, _questions in completed],
        "questions": [q for _result, qs in completed for q in qs],
    }


def _verify_fact_claim(
    claim: Claim,
    *,
    llm: BaseChatModel,
    search_client: SearchClient,
    max_results_per_query: int,
    claim_date: str = "",
) -> tuple[VerificationResult, list[Question]]:
    # Step 1: EPC/CC classification + multi NL questions + search queries
    claim_type, q_items = _request_fact_questions(claim, llm=llm, claim_date=claim_date)
    q_items = _augment_fact_questions(
        claim,
        claim_type=claim_type,
        q_items=q_items,
        claim_date=claim_date,
    )
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

    # Step 2: Evidence search
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
        verifier="fact",
        evidence=[evidence_summary(item) for item in evidence_results],
        reasoning=f"claim_type={claim_type}\nsearch_queries={all_queries}",
        sources=[item.url for item in evidence_results if item.url],
        metadata={
            "search_queries": all_queries,
            "claim_type": claim_type,
            **evidence_bundle.metadata,
        },
    )

    # Step 3: Per-question answer generation
    questions: list[Question] = []
    for qi in q_items:
        q_text = qi.get("question", "") or _question_text(claim, all_queries)
        a_result = _request_fact_answer(
            claim, question=q_text, evidence_text=evidence_text, claim_type=claim_type, llm=llm
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


def _request_fact_questions(
    claim: Claim,
    *,
    llm: BaseChatModel,
    claim_date: str = "",
) -> tuple[str, list[dict]]:
    response = llm.invoke(
        [
            SystemMessage(content=FACT_CHECK_SYSTEM),
            HumanMessage(
                content=FACT_QUESTION_USER.format(
                    claim=claim["text"],
                    context=claim.get("context", ""),
                    claim_date=claim_date or "(정보 없음)",
                ) + lang_instruction(claim)
            ),
        ]
    )
    parsed = parse_json_with_fallback(message_content(response.content))
    if isinstance(parsed, dict):
        claim_type = parsed.get("claim_type", "EPC")
        questions = parsed.get("questions")
        if isinstance(questions, list) and questions:
            return claim_type, questions
        # backward compat: old {\"question\": \"...\", \"search_queries\": [...]} format
        question = parsed.get("question", "")
        search_queries = parsed.get("search_queries", [])
        if question:
            return claim_type, [{"question": question, "search_queries": search_queries}]
    return "EPC", []


def _augment_fact_questions(
    claim: Claim,
    *,
    claim_type: str,
    q_items: list[dict],
    claim_date: str = "",
    max_questions: int = 7,
) -> list[dict]:
    """Add type-specific AVeriTeC QA atoms without sample-specific wording."""
    claim_text = claim["text"]
    normalized_type = str(claim_type or "EPC").upper()
    if normalized_type not in {"EPC", "CC"}:
        normalized_type = "CC" if _looks_causal(claim_text) else "EPC"

    seeds = _causal_question_seeds(claim_text) if normalized_type == "CC" else _event_question_seeds(
        claim_text,
        claim_date=claim_date,
    )
    return _merge_question_items(q_items, seeds, max_questions=max_questions)


def _event_question_seeds(claim_text: str, *, claim_date: str = "") -> list[dict]:
    queries = _claim_queries(claim_text)
    seeds = [
        {
            "atom": "claim_truth",
            "question": f"What evidence confirms or refutes this claim: {_question_claim_fragment(claim_text)}",
            "search_queries": queries,
        },
        {
            "atom": "direct_statement",
            "question": f"What did the named person, organization, or official source say about this claim: {_question_claim_fragment(claim_text)}",
            "search_queries": _claim_queries(claim_text, "denied OR statement OR official"),
        },
        {
            "atom": "correct_fact",
            "question": f"What is the correct fact if this claim is false: {_question_claim_fragment(claim_text)}",
            "search_queries": _claim_queries(claim_text, "fact check OR false OR debunked"),
        },
    ]
    if claim_date:
        seeds.append(
            {
                "atom": "claim_date",
                "question": "When was this claim made or when did the event in the claim occur?",
                "search_queries": _claim_queries(claim_text, claim_date),
            }
        )
    return seeds


def _causal_question_seeds(claim_text: str) -> list[dict]:
    return [
        {
            "atom": "causal_truth",
            "question": f"Is the causal relation in this claim established: {_question_claim_fragment(claim_text)}",
            "search_queries": _claim_queries(claim_text, "cause effect evidence"),
        },
        {
            "atom": "causal_evidence",
            "question": f"What direct evidence links the cause and effect in this claim: {_question_claim_fragment(claim_text)}",
            "search_queries": _claim_queries(claim_text, "evidence causal relationship"),
        },
        {
            "atom": "authority",
            "question": f"What do reliable sources say about this causal claim: {_question_claim_fragment(claim_text)}",
            "search_queries": _claim_queries(claim_text, "official scientific evidence fact check"),
        },
    ]


def _looks_causal(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "cause",
            "caused",
            "causes",
            "lead to",
            "leads to",
            "led to",
            "because",
            "due to",
            "resulted in",
            "responsible for",
        )
    )


def _merge_question_items(
    generated_items: list[dict],
    seed_items: list[dict],
    *,
    max_questions: int,
) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    covered_atoms = _covered_fact_atoms(generated_items)
    candidates = [
        *(_normalize_question_item(item, generated=True) for item in generated_items),
        *(
            _normalize_question_item(item, generated=False)
            for item in seed_items
            if item.get("atom") not in covered_atoms
        ),
    ]
    candidates = [item for item in candidates if item]
    candidates.sort(key=_question_priority)
    for item in candidates:
        question = item["question"]
        key = question.lower()
        if key in seen:
            continue
        merged.append({"question": question, "search_queries": item["search_queries"]})
        seen.add(key)
        if len(merged) >= max_questions:
            break
    return merged


def _normalize_question_item(item: dict, *, generated: bool) -> dict:
    question = str(item.get("question", "")).strip()
    if not question:
        return {}
    search_queries = item.get("search_queries", [])
    if not isinstance(search_queries, list):
        search_queries = []
    return {
        "question": question,
        "search_queries": search_queries or [question],
        "generated": generated,
        "atom": item.get("atom", ""),
    }


def _covered_fact_atoms(items: list[dict]) -> set[str]:
    text = " ".join(
        str(item.get("question", ""))
        for item in items
        if not _is_generic_question(str(item.get("question", "")))
    ).lower()
    covered: set[str] = set()
    if any(marker in text for marker in ("did ", "does ", "is it true", "confirm", "refute", "happen", "occur")):
        covered.add("claim_truth")
        covered.add("causal_truth")
    if any(marker in text for marker in ("said", "say", "statement", "quote", "comment", "denied", "official")):
        covered.add("direct_statement")
        covered.add("authority")
    if any(marker in text for marker in ("correct", "actual", "true fact", "false", "debunk")):
        covered.add("correct_fact")
    if any(marker in text for marker in ("when", "date", "claim date", "occur", "year")):
        covered.add("claim_date")
    if any(marker in text for marker in ("cause", "causal", "lead to", "leads to", "effect", "link")):
        covered.add("causal_truth")
        covered.add("causal_evidence")
    return covered


def _question_priority(item: dict) -> tuple[int, int, str]:
    question = str(item.get("question", "")).lower()
    generated_rank = 0 if item.get("generated") else 1
    broad_penalty = 2 if _is_generic_question(question) else 0
    return (broad_penalty, generated_rank, question)


def _is_generic_question(question: str) -> bool:
    lower = question.lower()
    return any(
        marker in lower
        for marker in (
            "valid as of",
            "alternative view",
            "alternative perspective",
            "counterargument",
            "context",
            "background",
            "circumstances",
            "impact",
            "reason",
            "other view",
            "exception",
            "what evidence supports",
            "what evidence links",
            "authoritative sources say",
            "reliable sources say",
        )
    )


def _question_claim_fragment(claim_text: str, max_chars: int = 180) -> str:
    fragment = " ".join(claim_text.split())
    if len(fragment) <= max_chars:
        return fragment
    return fragment[:max_chars].rsplit(" ", 1)[0]


def _claim_queries(claim_text: str, suffix: str = "") -> list[str]:
    base = _truncate_query(" ".join(claim_text.split()))
    if suffix:
        return [_truncate_query(f"{base} {suffix}"), base]
    return [base, _truncate_query(f"{base} fact check")]


def _truncate_query(query: str, max_chars: int = 360) -> str:
    query = " ".join(query.split())
    if len(query) <= max_chars:
        return query
    return query[:max_chars].rsplit(" ", 1)[0] or query[:max_chars]


def _request_fact_answer(
    claim: Claim,
    *,
    question: str,
    evidence_text: str,
    claim_type: str = "EPC",
    llm: BaseChatModel,
) -> dict[str, Any]:
    response = llm.invoke(
        [
            SystemMessage(content=FACT_CHECK_SYSTEM),
            HumanMessage(
                content=FACT_ANSWER_USER.format(
                    claim=claim["text"],
                    question=question,
                    evidence=evidence_text or "(검색 증거 없음)",
                    claim_type=claim_type,
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
            verifier="fact",
        )
    except Exception:
        logger.exception("fact_check_node: search failed")
        return SearchEvidenceBundle(results=[], metadata={})


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
        claim_id=claim["id"],
    )


def _question_text(claim: Claim, queries: list[str]) -> str:
    return rule_based_question(claim, queries)
