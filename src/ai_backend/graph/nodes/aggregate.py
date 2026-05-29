"""Aggregate verification node."""

from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from ai_backend.core.parsing import parse_json_with_fallback
from ai_backend.core.verification import message_content, normalize_issue_judgment
from ai_backend.graph.prompts.aggregate import AGGREGATE_SYSTEM, AGGREGATE_USER
from ai_backend.graph.state import (
    Claim,
    FinalGrade,
    FinalIssue,
    FinalReport,
    GraphState,
    Label,
    Question,
    VerificationResult,
)

logger = logging.getLogger(__name__)

_VERIFIER_LABELS = {
    "fact": "사실관계",
    "source": "출처",
    "recency": "최신성",
    "numeric": "수치",
}

_VERDICT_PRIORITY = {
    "PASS": 0,
    "WARNING": 1,
    "UNVERIFIABLE": 2,
    "FAIL": 3,
}

_AVERITEC_AGGREGATE_SYSTEM = """You are an AVeriTeC-style fact-checking judge.
Given extracted sub-claims and QA evidence, predict exactly one veracity label.

Allowed labels:
- Supported
- Refuted
- Not Enough Evidence
- Conflicting Evidence/Cherrypicking

Use only the QA evidence. If the QA evidence is missing, unanswerable, or too weak
to decide the claim, choose "Not Enough Evidence". Return only JSON:
{"label": "...", "justification": "..."}"""

_AVERITEC_AGGREGATE_USER = """Claims:
{claims}

QA evidence:
{questions}
"""

_LABELS: set[Label] = {
    "Supported",
    "Refuted",
    "Not Enough Evidence",
    "Conflicting Evidence/Cherrypicking",
}


def _label_from_report(final_report: FinalReport) -> Label:
    """Map the legacy final report to an AVeriTeC label without changing the API."""
    issues = final_report["issues"]
    if any(issue["judgment"] == "FAIL" for issue in issues):
        return "Refuted"
    if issues:
        return "Conflicting Evidence/Cherrypicking"
    return "Supported"


def _predict_averitec_label(
    claims: list[Claim],
    questions: list[Question],
    *,
    llm: BaseChatModel,
    fallback: tuple[Label, str],
) -> tuple[Label, str]:
    """Predict AVeriTeC label from QA evidence, with a conservative fallback."""
    if not _has_answered_evidence(questions):
        return (
            "Not Enough Evidence",
            "No answerable QA evidence was collected for the claim.",
        )

    try:
        response = llm.invoke(
            [
                SystemMessage(content=_AVERITEC_AGGREGATE_SYSTEM),
                HumanMessage(
                    content=_AVERITEC_AGGREGATE_USER.format(
                        claims=json.dumps(_claims_payload(claims), ensure_ascii=False, indent=2),
                        questions=json.dumps(
                            _questions_payload(questions),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ),
            ]
        )
        parsed = parse_json_with_fallback(message_content(response.content))
    except Exception:
        logger.exception("aggregate_node: AVeriTeC label prediction failed")
        return fallback

    if not isinstance(parsed, dict):
        return fallback
    return _normalize_averitec_prediction(parsed, fallback=fallback)


def _heuristic_label_from_questions(
    questions: list[Question],
    *,
    fallback: FinalReport,
) -> tuple[Label, str]:
    """Fallback label when no AVeriTeC judge LLM is available."""
    if not _has_answered_evidence(questions):
        return (
            "Not Enough Evidence",
            "No answerable QA evidence was collected for the claim.",
        )
    return _label_from_report(fallback), fallback["summary"]


def _has_answered_evidence(questions: list[Question]) -> bool:
    return any(
        answer["answer_type"] != "Unanswerable" and answer["answer"].strip()
        for question in questions
        for answer in question["answers"]
    )


def _normalize_averitec_prediction(
    value: dict[str, Any],
    *,
    fallback: tuple[Label, str],
) -> tuple[Label, str]:
    raw_label = str(value.get("label") or "").strip()
    label = cast(Label, raw_label) if raw_label in _LABELS else fallback[0]
    justification = str(value.get("justification") or "").strip() or fallback[1]
    return label, justification


def aggregate_node(
    state: GraphState,
    *,
    llm: BaseChatModel | None = None,
) -> dict[str, FinalGrade | FinalReport | Label | str]:
    """Aggregate outputs for both legacy service UI and AVeriTeC evaluation."""
    started = perf_counter()
    verification_results = _all_verification_results(state)
    questions = state.get("questions", [])
    is_averitec = state.get("run_mode") == "averitec"
    logger.info(
        "aggregate_node started claims=%d results=%d questions=%d",
        len(state["claims"]),
        len(verification_results),
        len(questions),
    )
    if not verification_results:
        logger.warning(
            "aggregate_node skipped no verification results elapsed=%.2fs",
            perf_counter() - started,
        )
        final_report = FinalReport(
            final_grade="확인 필요",
            summary="검증 결과가 없어 종합 판정을 수행할 수 없습니다.",
            issues=[],
        )
        return {
            "label": "Not Enough Evidence",
            "justification": final_report["summary"],
            "final_grade": final_report["final_grade"],
            "final_report": final_report,
        }

    fallback = _heuristic_aggregate(state["claims"], verification_results)
    label_fallback = (
        _heuristic_label_from_questions(questions, fallback=fallback)
        if is_averitec
        else (state.get("label", "Not Enough Evidence"), state.get("justification", ""))
    )
    if llm is None:
        logger.info("aggregate_node using heuristic aggregate without LLM")
        final_report = fallback
        label, justification = label_fallback
        logger.info(
            "aggregate_node finished elapsed=%.2fs label=%s final_grade=%s issues=%d",
            perf_counter() - started,
            label,
            final_report["final_grade"],
            len(final_report["issues"]),
        )
        return {
            "label": label,
            "justification": justification,
            "final_grade": final_report["final_grade"],
            "final_report": final_report,
        }

    try:
        aggregate: dict[str, Any] = _request_aggregate(
            state["claims"], verification_results, llm=llm
        )
    except Exception:
        logger.exception("aggregate_node: LLM aggregation failed; using heuristic fallback")
        aggregate = dict(fallback)

    aggregate_payload = aggregate if aggregate else dict(fallback)
    final_report = _normalize_final_report(aggregate_payload, fallback=fallback)
    label, justification = label_fallback
    if is_averitec:
        label, justification = _predict_averitec_label(
            state["claims"],
            questions,
            llm=llm,
            fallback=label_fallback,
        )
    logger.info(
        "aggregate_node finished elapsed=%.2fs label=%s final_grade=%s issues=%d",
        perf_counter() - started,
        label,
        final_report["final_grade"],
        len(final_report["issues"]),
    )
    return {
        "label": label,
        "justification": justification,
        "final_grade": final_report["final_grade"],
        "final_report": final_report,
    }


def _request_aggregate(
    claims: list[Claim],
    verification_results: list[VerificationResult],
    *,
    llm: BaseChatModel,
) -> dict[str, Any]:
    response = llm.invoke(
        [
            SystemMessage(content=AGGREGATE_SYSTEM),
            HumanMessage(
                content=AGGREGATE_USER.format(
                    claims=json.dumps(_claims_payload(claims), ensure_ascii=False, indent=2),
                    verification_results=json.dumps(
                        _results_payload(verification_results),
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            ),
        ]
    )
    parsed = parse_json_with_fallback(message_content(response.content))
    return parsed if isinstance(parsed, dict) else {}


def _heuristic_aggregate(
    # 이거를 최종 판단과정에서 약간의 로직을 변경해야 할 것으로 보임.
    # Fail이 없을 경우 통과 시킨다던지... (수정된 코드에 따르면 기준이 매우 엄격함 -> 거의 모든 자료에서 확인 필요...)
    claims: list[Claim],
    verification_results: list[VerificationResult],
) -> FinalReport:
    issues = _issues_from_results(claims, verification_results)
    has_fail = any(
        result["verdict"] in {"FAIL", "UNVERIFIABLE"} for result in verification_results
    )
    has_warning = any(result["verdict"] == "WARNING" for result in verification_results)

    if has_fail:
        final_grade: FinalGrade = "확인 필요"
        summary = "명백한 오류 또는 검증 불가 항목이 있어 확인이 필요합니다."
    elif has_warning:
        final_grade = "주의"
        summary = "치명적인 오류는 없지만 일부 항목은 보완 확인이 필요합니다."
    else:
        final_grade = "통과"
        summary = "모든 검증 노드에서 치명적인 오류가 발견되지 않았습니다."

    return FinalReport(final_grade=final_grade, summary=summary, issues=issues)


def _issues_from_results(
    claims: list[Claim],
    verification_results: list[VerificationResult],
) -> list[FinalIssue]:
    claim_by_id = {claim["id"]: claim for claim in claims}
    issues: list[FinalIssue] = []
    results_by_claim = _results_grouped_by_claim(claims, verification_results)
    for claim_id, claim_results in results_by_claim.items():
        claim = claim_by_id.get(claim_id)
        sorted_results = sorted(
            claim_results,
            key=lambda r: (_VERDICT_PRIORITY[r["verdict"]], r["confidence"]),
            reverse=True,
        )
        for result in sorted_results:
            if result["verdict"] not in {"WARNING", "FAIL"}:
                continue
            issues.append(
                FinalIssue(
                    node=_VERIFIER_LABELS.get(result["verifier"], result["verifier"]),
                    highlighted_text=claim["text"] if claim else claim_id,
                    judgment=result["verdict"],
                    problem=_extract_human_reason(result["reasoning"]),
                    suggestion=_issue_suggestion(result),
                )
            )
    return issues


def _results_grouped_by_claim(
    claims: list[Claim],
    verification_results: list[VerificationResult],
) -> dict[str, list[VerificationResult]]:
    grouped: dict[str, list[VerificationResult]] = {claim["id"]: [] for claim in claims}
    for result in verification_results:
        grouped.setdefault(result["claim_id"], []).append(result)
    return {claim_id: results for claim_id, results in grouped.items() if results}


_TERM_REPLACEMENTS = [
    ("Claim", "자료"),
    ("claim", "자료"),
    ("Evidence", "AI검증"),
    ("evidence", "AI검증"),
]


def _replace_terms(text: str) -> str:
    for old, new in _TERM_REPLACEMENTS:
        text = text.replace(old, new)
    return text


def _extract_human_reason(reasoning: str) -> str:
    """reasoning 문자열에서 사람이 읽을 수 있는 reason= 부분만 추출."""
    for line in reasoning.splitlines():
        if line.startswith("reason="):
            return line[len("reason="):].strip()
    return reasoning.strip()


def _extract_suggestion(reasoning: str) -> str:
    """reasoning 문자열에서 suggestion= 부분만 추출."""
    for line in reasoning.splitlines():
        if line.startswith("suggestion="):
            return line[len("suggestion="):].strip()
    return ""


def _issue_suggestion(result: VerificationResult) -> str:
    suggestion = _extract_suggestion(result["reasoning"])
    if suggestion:
        return _replace_terms(suggestion)
    if result["verifier"] == "numeric":
        return "수치, 기준연도, 계산식을 근거 자료와 다시 대조하세요."
    if result["verifier"] == "source":
        return "구체적인 원문 URL 또는 문헌 정보를 보완하세요."
    if result["verifier"] == "recency":
        return "최신 자료 기준으로 표현과 시점을 갱신하세요."
    return "근거 자료와 충돌하는 표현을 수정하세요."


def _all_verification_results(state: GraphState) -> list[VerificationResult]:
    return [
        *state.get("fact_results", []),
        *state.get("source_results", []),
        *state.get("recency_results", []),
        *state.get("numeric_results", []),
    ]


def _claims_payload(claims: list[Claim]) -> list[dict[str, Any]]:
    return [
        {
            "id": claim["id"],
            "text": claim["text"],
            "type": claim["type"],
            "context": claim["context"],
        }
        for claim in claims
    ]


def _results_payload(results: list[VerificationResult]) -> list[dict[str, Any]]:
    return [
        {
            "claim_id": result["claim_id"],
            "node": _VERIFIER_LABELS.get(result["verifier"], result["verifier"]),
            "verifier": result["verifier"],
            "judgment": result["verdict"],
            "confidence": result["confidence"],
            "reason": result["reasoning"],
            "evidence": result["evidence"],
            "sources": result["sources"],
        }
        for result in results
    ]


def _questions_payload(questions: list[Question]) -> list[dict[str, Any]]:
    return [
        {
            "question": question["question"],
            "answers": [
                {
                    "answer": answer["answer"],
                    "answer_type": answer["answer_type"],
                    "source_url": answer["source_url"],
                }
                for answer in question["answers"]
            ],
        }
        for question in questions
    ]


def _normalize_final_report(value: dict[str, Any], *, fallback: FinalReport) -> FinalReport:
    final_grade = _normalize_final_grade(value.get("final_grade"), fallback["final_grade"])
    summary = str(value.get("summary") or fallback["summary"])
    # LLM이 요약한 issues 대신 raw results에서 직접 추출한 WARNING/FAIL 항목을 사용
    issues = fallback["issues"]
    return FinalReport(final_grade=final_grade, summary=summary, issues=issues)


def _normalize_final_grade(value: Any, fallback: FinalGrade) -> FinalGrade:
    grade = str(value or "").strip()
    return cast(FinalGrade, grade) if grade in {"통과", "주의", "확인 필요"} else fallback


def _normalize_issues(value: Any) -> list[FinalIssue]:
    if not isinstance(value, list):
        return []
    issues: list[FinalIssue] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        issues.append(
            FinalIssue(
                node=str(item.get("node") or ""),
                highlighted_text=str(item.get("highlighted_text") or ""),
                judgment=normalize_issue_judgment(item.get("judgment")),
                problem=str(item.get("problem") or ""),
                suggestion=str(item.get("suggestion") or ""),
            )
        )
    return issues
