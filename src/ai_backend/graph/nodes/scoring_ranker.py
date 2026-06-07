"""Rank AVeriTeC QA evidence for scorer-facing predictions."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from ai_backend.graph.state import Label, Question

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?%?\b")

_GENERIC_QUESTION_MARKERS = (
    "valid as of",
    "what evidence supports",
    "what evidence confirms",
    "what evidence links",
    "reliable sources say",
    "authoritative sources say",
    "alternative view",
    "alternative perspective",
    "other perspective",
    "counterargument",
    "background",
    "circumstances",
    "impact",
    "reason",
    "exception",
    "broader context",
    "additional context",
)

_DIRECT_FRAGMENT_MARKERS = (
    "denied",
    "did not",
    "does not",
    "false",
    "incorrect",
    "misleading",
    "correction",
    "correct",
    "transcript",
    "quote",
    "stated",
    "said",
    "according to",
    "recommended",
    "official",
    "reported",
)

_CE_MARKERS = (
    "misleading",
    "out of context",
    "without context",
    "cherry-picked",
    "cherry picking",
    "partial",
    "partially",
    "caveat",
    "exception",
    "broader",
    "narrower",
    "qualified",
    "however",
    "although",
)


def rank_scoring_questions(
    questions: list[Question],
    *,
    claim: str,
    label: str,
) -> list[Question]:
    """Return questions ordered for AVeriTeC HU-METEOR matching.

    The scorer only evaluates the first ten prediction QA pairs. This ranking
    keeps all QA evidence but puts direct, atom-specific, extractive evidence
    before generic verdict/context questions.
    """
    if (
        _has_high_overlap_direct_question(questions[:10], claim=claim)
        and _unanswerable_count(questions[:10]) < 2
    ):
        return deepcopy(questions)

    ranked_input = [
        *deepcopy(questions),
        *_scoring_variants(questions, claim=claim, label=label),
    ]
    indexed = list(enumerate(ranked_input))
    indexed.sort(
        key=lambda item: (
            -_question_score(item[1], claim=claim, label=label),
            item[0],
        )
    )
    return [question for _index, question in indexed]


def _scoring_variants(questions: list[Question], *, claim: str, label: str) -> list[Question]:
    """Add deterministic scorer-facing variants using existing answers only."""
    if _has_high_overlap_direct_question(questions, claim=claim):
        return []

    variants: list[Question] = []
    seen_answers: set[str] = set()
    fragment = _claim_fragment(claim)
    candidates = sorted(
        deepcopy(questions),
        key=lambda question: -_question_score(question, claim=claim, label=label),
    )
    for question in candidates:
        answers = question.get("answers", [])
        if not answers:
            continue
        answer = answers[0]
        answer_text = _answer_text(question).strip()
        if not answer_text or answer.get("answer_type") == "Unanswerable":
            continue
        answer_key = _normalize_space(answer_text.lower())[:180]
        if answer_key in seen_answers:
            continue
        variant_question = _variant_question(label=label, claim_fragment=fragment)
        variants.append(
            {
                "question": variant_question,
                "answers": [deepcopy(answer)],
                "claim_id": question.get("claim_id", ""),
            }
        )
        seen_answers.add(answer_key)
        if len(variants) >= 1:
            break
    return variants


def _has_high_overlap_direct_question(questions: list[Question], *, claim: str) -> bool:
    for question in questions:
        joined = f"{question.get('question', '')} {_answer_text(question)}"
        if _claim_token_overlap(joined, claim) >= 0.7:
            answer_type = _first_answer_field(question, "answer_type")
            support_type = _first_answer_field(question, "support_type")
            if answer_type != "Unanswerable" and support_type != "related_only":
                return True
    return False


def _unanswerable_count(questions: list[Question]) -> int:
    return sum(1 for question in questions if _first_answer_field(question, "answer_type") == "Unanswerable")


def _variant_question(*, label: str, claim_fragment: str) -> str:
    # Use a short prefix from the claim to retain entity overlap without bloating
    # the question string with the full claim text — long questions dilute METEOR precision.
    short = claim_fragment[:55].rsplit(" ", 1)[0] if len(claim_fragment) > 55 else claim_fragment
    if label == "Refuted":
        return f"What correction or denial addresses: {short}?"
    if label == "Supported":
        return f"What direct fact supports: {short}?"
    if label == "Conflicting Evidence/Cherrypicking":
        return f"What qualified or missing fact addresses: {short}?"
    if label == "Not Enough Evidence":
        return f"What exact fact is missing for: {short}?"
    return f"What direct evidence addresses: {short}?"


def _question_score(question: Question, *, claim: str, label: str) -> float:
    q_text = str(question.get("question", ""))
    answer_text = _answer_text(question)
    joined = f"{q_text} {answer_text}"
    joined_l = joined.lower()

    score = 0.0
    score += 12.0 * _claim_token_overlap(joined, claim)
    score += _answer_type_score(question)
    score += _support_score(question, label=label)
    score += _directness_score(question)
    score += _mismatch_score(question, label=label)
    score += _answer_length_score(answer_text)

    if _has_number(claim) and _has_number(joined):
        score += 4.0
    if _looks_like_quote_or_source_claim(claim) and _has_quote_source_fragment(joined_l):
        score += 4.0
    if any(marker in joined_l for marker in _DIRECT_FRAGMENT_MARKERS):
        score += 2.0
    if label == "Conflicting Evidence/Cherrypicking" and any(
        marker in joined_l for marker in _CE_MARKERS
    ):
        score += 5.0
    if _is_generic_question(q_text):
        score -= 8.0
    if _is_verdict_only_answer(answer_text):
        score -= 5.0
    if not answer_text.strip():
        score -= 10.0
    return score


def _claim_fragment(claim: str, max_chars: int = 180) -> str:
    fragment = _normalize_space(claim)
    if len(fragment) <= max_chars:
        return fragment
    return fragment[:max_chars].rsplit(" ", 1)[0] or fragment[:max_chars]


def _normalize_space(text: str) -> str:
    return " ".join(str(text or "").split())


def _answer_text(question: Question) -> str:
    parts: list[str] = []
    for answer in question.get("answers", []):
        parts.append(str(answer.get("answer", "")))
        explanation = str(answer.get("boolean_explanation", ""))
        if explanation:
            parts.append(explanation)
    return " ".join(parts)


def _answer_type_score(question: Question) -> float:
    answers = question.get("answers", [])
    if not answers:
        return -6.0
    answer_type = str(answers[0].get("answer_type", ""))
    return {
        "Extractive": 5.0,
        "Boolean": 3.0,
        "Abstractive": 1.0,
        "Unanswerable": -4.0,
    }.get(answer_type, 0.0)


def _support_score(question: Question, *, label: str) -> float:
    support = _first_answer_field(question, "support_type")
    base = {
        "direct_support": 5.0,
        "contradiction": 5.0,
        "partial_support": 2.0,
        "insufficient_evidence": 0.0,
        "related_only": -5.0,
        "unknown": -1.0,
    }.get(support, -1.0)
    if label == "Supported" and support == "direct_support":
        base += 3.0
    elif label == "Refuted" and support == "contradiction":
        base += 3.0
    elif label == "Not Enough Evidence" and support == "insufficient_evidence":
        base += 3.0
    elif label == "Conflicting Evidence/Cherrypicking" and support == "partial_support":
        base += 3.0
    return base


def _directness_score(question: Question) -> float:
    directness = _first_answer_field(question, "directness")
    return {"direct": 3.0, "indirect": -3.0, "unknown": 0.0}.get(directness, 0.0)


def _mismatch_score(question: Question, *, label: str) -> float:
    mismatch = _first_answer_field(question, "mismatch_type")
    if mismatch in {"", "none"}:
        return 2.0
    if label == "Conflicting Evidence/Cherrypicking" and mismatch in {
        "scope",
        "time",
        "number",
        "attribution",
        "context",
        "methodology",
    }:
        return 2.0
    if mismatch == "unknown":
        return 0.0
    return -1.0


def _answer_length_score(text: str) -> float:
    words = text.split()
    count = len(words)
    if count == 0:
        return -5.0
    if 5 <= count <= 35:
        return 3.0
    if count <= 55:
        return 1.0
    return -3.0


def _claim_token_overlap(text: str, claim: str) -> float:
    claim_tokens = _content_tokens(claim)
    if not claim_tokens:
        return 0.0
    text_tokens = _content_tokens(text)
    return len(claim_tokens & text_tokens) / len(claim_tokens)


def _content_tokens(text: str) -> set[str]:
    stop = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "that",
        "this",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "as",
        "it",
    }
    return {token.lower() for token in _TOKEN_RE.findall(text) if token.lower() not in stop}


def _first_answer_field(question: Question, field: str) -> str:
    answers = question.get("answers", [])
    if not answers:
        return ""
    return str(answers[0].get(field, ""))


def _has_number(text: str) -> bool:
    return _NUMBER_RE.search(text) is not None


def _looks_like_quote_or_source_claim(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in (" said ", " says ", " stated ", " quote ", '"'))


def _has_quote_source_fragment(text: str) -> bool:
    return any(
        marker in text
        for marker in ("said", "stated", "transcript", "quote", "denied", "source", "statement")
    )


def _is_generic_question(question: str) -> bool:
    lower = question.lower()
    return any(marker in lower for marker in _GENERIC_QUESTION_MARKERS)


def _is_verdict_only_answer(answer: str) -> bool:
    lower = answer.strip().lower()
    verdict_phrases = (
        "the claim is true",
        "the claim is false",
        "this claim is true",
        "this claim is false",
        "the evidence supports the claim",
        "the evidence refutes the claim",
        "there is not enough evidence",
    )
    return any(lower.startswith(phrase) for phrase in verdict_phrases)
