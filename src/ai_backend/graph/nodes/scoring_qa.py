"""Create compact AVeriTeC scoring QA as a postprocess step."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from ai_backend.core.parsing import parse_json_with_fallback
from ai_backend.core.verification import message_content
from ai_backend.graph.state import Claim, Question

logger = logging.getLogger(__name__)

_SCORING_QA_SYSTEM = """You rewrite existing verification QA into AVeriTeC scoring evidence.

Goal:
- Produce compact QA evidence that is easy to match against human gold QA.
- Do not change the veracity label and do not add facts not present in the input.
- Use only the supplied QA evidence. You may rewrite, merge, shorten, and reorder it.

General rules:
- Return at most 10 questions.
- Each question must ask one atomic fact.
- Prefer questions about exact entity/action/date/location/number/unit/source.
- Prefer questions that can be answered by one evidence sentence or fragment.
- For Refuted claims, include the direct correction, denial, debunking statement,
  or true alternative fact when present.
- For Supported claims, include the direct supporting fact, date, number, or quote.
- For Not Enough Evidence, include the missing exact fact or the strongest
  available "not established" fragment.
- For Conflicting Evidence/Cherrypicking, include the exact true atom and the
  missing/cherry-picked/qualified atom.
- Avoid broad background, cause, impact, reason, and general context questions
  unless that atom is part of the claim.
- Answers should usually be 5-30 words and should preserve evidence wording when
  possible. Avoid long verdict explanations.
- Boolean answers must use answer "Yes" or "No"; keep boolean_explanation short
  and evidence-like because scorers append it to the answer.

Return only JSON:
{"questions": [
  {"question": "...", "answers": [
    {"answer": "...", "answer_type": "Extractive|Abstractive|Boolean|Unanswerable", "boolean_explanation": "...", "source_url": "..."}
  ]}
]}"""

_SCORING_QA_USER = """Claim:
{claim}

Predicted label:
{label}

Claim date:
{claim_date}

Existing QA evidence:
{questions}
"""


def rewrite_scoring_questions(
    *,
    claim_text: str,
    label: str,
    questions: list[Question],
    llm: BaseChatModel,
    claim_date: str = "",
    claim_id: str = "claim",
) -> list[Question]:
    """Rewrite one prediction's QA into compact AVeriTeC scoring evidence."""
    claim = {
        "id": claim_id,
        "text": claim_text,
        "content_hash": "",
        "document_id": "",
        "type": [],
        "context": "",
        "citations": [],
        "extracted_at": "",
        "parent_claim_id": None,
    }
    return _rewrite_claim_questions(
        claim=claim,
        label=label,
        claim_date=claim_date,
        questions=questions,
        llm=llm,
    )


def _rewrite_claim_questions(
    *,
    claim: Claim,
    label: str,
    claim_date: str,
    questions: list[Question],
    llm: BaseChatModel,
) -> list[Question]:
    try:
        response = llm.invoke(
            [
                SystemMessage(content=_SCORING_QA_SYSTEM),
                HumanMessage(
                    content=_SCORING_QA_USER.format(
                        claim=claim["text"],
                        label=label,
                        claim_date=claim_date,
                        questions=json.dumps(
                            _questions_payload(questions),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ),
            ]
        )
    except Exception:
        logger.exception("scoring_qa_node LLM rewrite failed claim_id=%s", claim["id"])
        return []

    parsed = parse_json_with_fallback(message_content(response.content))
    if not isinstance(parsed, dict):
        return []
    raw_questions = parsed.get("questions")
    if not isinstance(raw_questions, list):
        return []

    rewritten: list[Question] = []
    for raw_question in raw_questions[:10]:
        question = _normalize_question(raw_question, claim_id=claim["id"])
        if question is not None:
            rewritten.append(question)
    return rewritten


def _questions_payload(questions: list[Question]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for question in questions[:16]:
        answers = []
        for answer in question.get("answers", [])[:2]:
            answers.append(
                {
                    "answer": answer.get("answer", ""),
                    "answer_type": answer.get("answer_type", ""),
                    "boolean_explanation": answer.get("boolean_explanation", ""),
                    "source_url": answer.get("source_url", ""),
                    "support_type": answer.get("support_type", ""),
                    "directness": answer.get("directness", ""),
                    "mismatch_type": answer.get("mismatch_type", ""),
                }
            )
        payload.append(
            {
                "question": question.get("question", ""),
                "answers": answers,
            }
        )
    return payload


def _normalize_question(raw_question: Any, *, claim_id: str) -> Question | None:
    if not isinstance(raw_question, dict):
        return None
    q_text = str(raw_question.get("question", "")).strip()
    raw_answers = raw_question.get("answers", [])
    if isinstance(raw_answers, dict):
        raw_answers = [raw_answers]
    if not q_text or not isinstance(raw_answers, list):
        return None

    answers = []
    for raw_answer in raw_answers[:1]:
        if not isinstance(raw_answer, dict):
            continue
        answer_text = str(raw_answer.get("answer", "")).strip()
        answer_type = str(raw_answer.get("answer_type", "Extractive")).strip()
        if answer_type not in {"Extractive", "Abstractive", "Boolean", "Unanswerable"}:
            answer_type = "Extractive"
        if answer_type == "Boolean":
            answer_text = "No" if answer_text.lower().startswith("no") else "Yes"
        if not answer_text:
            continue
        answer = {
            "answer": answer_text,
            "answer_type": answer_type,
            "source_url": str(raw_answer.get("source_url", "")).strip(),
        }
        explanation = str(raw_answer.get("boolean_explanation", "")).strip()
        if answer_type == "Boolean" and explanation:
            answer["boolean_explanation"] = explanation
        answers.append(answer)

    if not answers:
        return None
    return {"question": q_text, "answers": answers, "claim_id": claim_id}
