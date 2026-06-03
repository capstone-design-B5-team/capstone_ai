"""Aggregate verification node."""

from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from ai_backend.core.parsing import parse_json_with_fallback
from ai_backend.core.verification import message_content
from ai_backend.graph.state import (
    Claim,
    ClaimLabel,
    GraphState,
    Label,
    Question,
)

logger = logging.getLogger(__name__)

_AVERITEC_AGGREGATE_SYSTEM = """You are an AVeriTeC-style fact-checking judge.
Given a claim and QA evidence, predict exactly one veracity label.

**YOU MUST write the "justification" field in the SAME language as the claim.**
한국어 주장이면 justification도 반드시 한국어로 작성하세요.

Label definitions and decision rules:

- Supported: The evidence clearly confirms the claim as stated.

- Refuted: The evidence clearly contradicts or disproves the claim.
  Use this when the claim is factually wrong, the event did not happen,
  the person did not say it, or the number is demonstrably incorrect.
  If the evidence consistently points in one direction against the claim, use Refuted.

- Conflicting Evidence/Cherrypicking: Use ONLY when:
  (a) different credible sources genuinely contradict each other (not just one source raising doubts),
  (b) the claim selectively uses outdated data while more recent data tells a different story,
  (c) the claim is technically true but deliberately omits context that reverses its meaning.

- Not Enough Evidence: Use ONLY when QA answers are truly irrelevant or unanswerable.
  Do NOT use this label if the answers contain any relevant information about the claim topic.

Key distinctions:
  Refuted vs CE — consistent evidence against the claim → Refuted.
                  genuinely split credible sources, or deliberate cherry-picking → CE.
  NEE vs others — if the answer addresses the claim topic at all, pick a verdict.
                  Reserve NEE for genuinely off-topic or empty answers.

Return only JSON: {"label": "...", "justification": "..."}
The justification must be a concise user-facing explanation (1-2 sentences).
REMINDER: justification language MUST match the claim language."""

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

_NEE_FALLBACK: tuple[Label, str] = (
    "Not Enough Evidence",
    "AVeriTeC 레이블 예측에 실패하여 기본값을 반환합니다.",
)


def _predict_averitec_label(
    claims: list[Claim],
    questions: list[Question],
    *,
    llm: BaseChatModel,
) -> tuple[Label, str]:
    """Predict AVeriTeC label from QA evidence."""
    if not _has_answered_evidence(questions):
        return (
            "Not Enough Evidence",
            "해당 주장에 대해 검증 가능한 근거를 찾지 못했습니다.",
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
        return _NEE_FALLBACK

    if not isinstance(parsed, dict):
        return _NEE_FALLBACK
    return _normalize_averitec_prediction(parsed, fallback=_NEE_FALLBACK)


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
) -> dict[str, Any]:
    """Aggregate QA evidence into per-claim labels."""
    started = perf_counter()
    questions = state.get("questions", [])
    claims = state["claims"]
    logger.info(
        "aggregate_node started claims=%d questions=%d",
        len(claims),
        len(questions),
    )

    if not _has_answered_evidence(questions):
        logger.warning(
            "aggregate_node skipped no answerable evidence elapsed=%.2fs",
            perf_counter() - started,
        )
        return {
            "claim_labels": [
                ClaimLabel(
                    claim_id=claim["id"],
                    label="Not Enough Evidence",
                    justification="검증 가능한 QA 증거가 없어 판정을 수행할 수 없습니다.",
                )
                for claim in claims
            ],
        }

    if llm is None:
        logger.warning("aggregate_node: no LLM provided, returning NEE")
        return {
            "claim_labels": [
                ClaimLabel(
                    claim_id=claim["id"],
                    label="Not Enough Evidence",
                    justification="LLM이 없어 판정을 수행할 수 없습니다.",
                )
                for claim in claims
            ],
        }

    # Per-claim aggregation
    claim_labels: list[ClaimLabel] = []
    for claim in claims:
        claim_questions = [q for q in questions if q.get("claim_id") == claim["id"]]
        label, justification = _predict_averitec_label([claim], claim_questions, llm=llm)
        claim_labels.append(ClaimLabel(
            claim_id=claim["id"],
            label=label,
            justification=justification,
        ))

    logger.info(
        "aggregate_node finished elapsed=%.2fs claim_labels=%d",
        perf_counter() - started,
        len(claim_labels),
    )
    return {"claim_labels": claim_labels}


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


def _questions_payload(questions: list[Question]) -> list[dict[str, Any]]:
    result = []
    for question in questions:
        answers = []
        for answer in question["answers"]:
            a: dict[str, Any] = {
                "answer": answer["answer"],
                "answer_type": answer["answer_type"],
                "source_url": answer["source_url"],
            }
            if answer.get("boolean_explanation"):
                a["boolean_explanation"] = answer["boolean_explanation"]
            answers.append(a)
        result.append({"question": question["question"], "answers": answers})
    return result
