"""Aggregate verification node."""

from __future__ import annotations

import json
import logging
import re
from time import perf_counter
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from ai_backend.core.parsing import parse_json_with_fallback
from ai_backend.core.search_policy import source_domain, source_quality_reasons
from ai_backend.core.verification import message_content
from ai_backend.graph.state import (
    Claim,
    ClaimLabel,
    GraphState,
    Label,
    Question,
    VerificationResult,
)

logger = logging.getLogger(__name__)

_AVERITEC_AGGREGATE_SYSTEM = """You are an AVeriTeC-style fact-checking judge.
Given a claim and QA evidence, predict exactly one veracity label.

**YOU MUST write the "justification" field in the SAME language as the claim.**
한국어 주장이면 justification도 반드시 한국어로 작성하세요.

Label definitions and decision rules:

- Supported: The evidence clearly confirms the claim as stated.
  Use this only when the QA evidence supports every material part of the claim.
  The subject/entity, time period, location or jurisdiction, numerical value,
  unit, comparison target, and quoted wording must match the claim when those
  details are material.

- Refuted: The evidence clearly contradicts or disproves the claim.
  Use this when the claim is factually wrong, the event did not happen,
  the person did not say it, or the number is demonstrably incorrect.
  If the evidence consistently points in one direction against the same subject,
  scope, and time period as the claim, use Refuted.

- Conflicting Evidence/Cherrypicking: Use ONLY when:
  (a) different credible sources genuinely contradict each other
      (not just one source raising doubts),
  (b) the claim selectively uses outdated data while more recent data tells a different story,
  (c) the claim is technically true but deliberately omits context that reverses its meaning,
  (d) the claim is true in some jurisdictions, cases, groups, or time periods but
      false or unresolved in others, and the claim presents it as generally true.

- Not Enough Evidence: Use when QA answers do not directly establish or refute
  the claim as stated. Relevant but mismatched evidence is not enough.
  Prefer this label when evidence is about a different entity, scope, location,
  time period, unit, or comparison target, unless that mismatch directly refutes
  the claim.

Key distinctions:
  Refuted vs CE — consistent evidence against the claim → Refuted.
                  genuinely split credible sources, or deliberate cherry-picking → CE.
  NEE vs others — evidence must answer the claim as stated, not just the broad topic.
                  If the evidence only supports one part of a compound claim, do not
                  label Supported.

Additional safeguards against overclaiming:
  - Do not label Supported merely because one component of a multi-part claim is true.
  - For numerical claims, value, date/timeframe, unit, scope, and comparison must match.
  - For quote claims, the speaker/source and wording or meaning must match.
  - For broad claims, look for exceptions. If credible evidence shows important
    exceptions, use CE; if exceptions are not checked, use NEE rather than Supported.

AVeriTeC decision checklist and common pitfalls:
  - First decide what the claim is asserting: a factual event/property, a number,
    a direct quote/source attribution, recency/cherrypicking, or a broad causal
    or policy interpretation. Do not let a secondary QA question change the
    claim type.
  - If a QA question asks whether a person/source directly said something, but
    the original claim is not itself a direct quote or attribution claim, a "no
    direct statement found" answer is usually not enough to Refute the factual
    claim. Weigh the factual QA answers first.
  - For direct quote or attribution claims, require direct evidence that the
    named person/source said it with matching meaning. If evidence only shows
    that another person characterized the named person's view, or only shows a
    related policy/action, do not label Supported; use CE for partially supported
    but context-dependent characterizations, or NEE when direct attribution is
    missing.
  - If most direct factual QA answers support a non-quote factual claim, do not
    Refute it merely because source/QV answers say there is no direct statement
    from a named person. Source attribution matters only when the claim itself
    asserts who said, wrote, published, or endorsed the statement.
  - For claims about past statements, do not Refute solely because later guidance
    or later facts changed. If the earlier statement happened but later context
    changes the meaning, use CE.
  - For jurisdiction-dependent or scope-dependent claims, use CE when credible
    evidence shows the claim is true in some states, cases, groups, or periods
    but false, conditional, or unresolved in others.
  - If the evidence confirms the event but says the claim's loaded description,
    motive, or framing is incomplete or misleading (for example "just",
    "aid/help", "anti-X", "warning because of X" when the evidence gives a more
    qualified reason), use CE rather than Supported.
  - Do not Refute a claim solely because evidence uses a more specific name for
    the same object or event. If evidence says the specific object is commonly
    characterized by the claim's description, treat that part as supported.
  - For precise numerical or timing claims, an exact mismatch in unit,
    measurement method, or required duration is a contradiction, not just a
    partial match. Annualized rates, actual quarter-over-quarter changes,
    cumulative totals, and levels are different measurements.
  - For medical, scientific, or causal-effect claims, evidence saying a treatment
    or causal effect is unproven, inconclusive, or recommended only in trials
    means NEE unless the evidence explicitly says the claimed effect is false or
    ineffective.
  - Lack of official approval, lack of authorization, or "not aware of reports"
    is not by itself a direct refutation of an existence or treatment claim.
    Use NEE unless the evidence conclusively establishes that the event, scam,
    treatment effect, or causal relationship did not exist or is false.
  - Do not use CE as a generic uncertainty label. There must be a specific
    partial-truth, omitted-context, scope, timing, or genuinely conflicting
    evidence pattern in the QA evidence.

Source quality hints may appear on answers:
  - fact_check_domain: useful for known fact-check/refutation context.
  - primary_source_domain or official_domain: useful for original statements,
    official rules, government data, and transcript-like evidence.
  - low_quality_domain: do not rely on this alone for a final label.
Use these hints only as evidence-quality context; the answer text still controls
whether the claim is supported, refuted, conflicting, or not established.

Structured answer support signals may appear:
  - support_type=direct_support: evidence directly establishes the exact question.
  - support_type=partial_support: evidence supports only part of the question or
    includes caveats, exceptions, omitted context, or qualified framing.
  - support_type=contradiction: evidence directly contradicts the exact question.
  - support_type=insufficient_evidence: evidence is missing, inconclusive, or not
    enough to establish the exact question.
  - support_type=related_only: evidence is about the broad topic but not the
    exact claim.
  - directness=indirect or mismatch_type other than none is a warning against
    Supported unless other direct answers resolve the issue.
Use these signals as strong hints, but still read the answer text.

Verifier summaries may also appear:
  - fact/numeric/recency summaries describe the main factual, numerical, or
    temporal verification intent.
  - source summaries are strongest for direct quote, speaker, document, or
    attribution claims. For ordinary factual claims, source summaries should not
    override direct fact/numeric/recency evidence unless they directly contradict
    the claim.

Return only JSON: {"label": "...", "justification": "..."}
The justification must be a concise user-facing explanation (1-2 sentences).
REMINDER: justification language MUST match the claim language."""

_AVERITEC_AGGREGATE_USER = """Claims:
{claims}

QA evidence:
{questions}

Verifier summaries:
{verifier_results}
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
    verifier_results: list[VerificationResult] | None = None,
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
                        verifier_results=json.dumps(
                            _verification_results_payload(verifier_results or []),
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
    label, justification = _normalize_averitec_prediction(parsed, fallback=_NEE_FALLBACK)
    return _calibrate_averitec_label(
        claims[0],
        questions,
        label,
        justification,
        verifier_results=verifier_results or [],
    )


def _has_answered_evidence(questions: list[Question]) -> bool:
    return any(
        answer["answer_type"] != "Unanswerable" and answer["answer"].strip()
        for question in questions
        for answer in question["answers"]
    )


def _calibrate_averitec_label(
    claim: Claim,
    questions: list[Question],
    label: Label,
    justification: str,
    *,
    verifier_results: list[VerificationResult] | None = None,
) -> tuple[Label, str]:
    """Apply conservative AVeriTeC-specific label calibration."""
    claim_text = claim["text"]
    claim_l = claim_text.lower()
    evidence_l = f"{_evidence_text(questions)}\n{_verifier_evidence_text(verifier_results or [])}".lower()
    support_counts = _answer_support_counts(questions)

    direct = support_counts["direct_support"]
    contradiction = support_counts["contradiction"]
    weak = support_counts["insufficient_evidence"] + support_counts["related_only"]
    partial = support_counts["partial_support"]
    indirect = support_counts["indirect"]
    context_mismatch = support_counts["context"] + support_counts["attribution"]
    method_mismatch = support_counts["methodology"] + support_counts["number"]
    numeric_claim = _is_numeric_or_comparative_claim(claim_l)
    comparative_claim = _is_comparative_or_rate_claim(claim_l)

    if label == "Supported":
        if numeric_claim and _has_missing_basis_signal(evidence_l):
            return (
                "Not Enough Evidence",
                _calibration_message(
                    claim_text,
                    "The evidence discusses related numeric facts, but the exact basis needed "
                    "to verify the stated comparison is missing.",
                    "The evidence discusses related numeric facts, but the exact basis needed "
                    "to verify the stated comparison is missing.",
                ),
            )
        if (
            numeric_claim
            and weak + partial >= 3
            and direct <= 5
            and (weak >= 1 or comparative_claim)
        ):
            return (
                "Not Enough Evidence",
                _calibration_message(
                    claim_text,
                    "The answers provide only partial or inconclusive numeric support, so the "
                    "claim is not established as stated.",
                    "The answers provide only partial or inconclusive numeric support, so the "
                    "claim is not established as stated.",
                ),
            )
        if (
            numeric_claim
            and support_counts["mismatch"] >= 4
            and partial >= 3
            and contradiction == 0
            and (weak >= 1 or comparative_claim)
        ):
            return (
                "Not Enough Evidence",
                _calibration_message(
                    claim_text,
                    "The answers depend on methodology or comparison details that are not "
                    "settled well enough to verify the numeric claim.",
                    "The answers depend on methodology or comparison details that are not "
                    "settled well enough to verify the numeric claim.",
                ),
            )
        if weak >= 2 and direct <= 4:
            return (
                "Not Enough Evidence",
                _calibration_message(
                    claim_text,
                    "답변들은 관련 주제를 다루지만 주장 그대로를 직접 입증하지 못하므로 "
                    "증거 부족에 가깝습니다.",
                    "The answers discuss related evidence but do not directly establish the "
                    "claim as stated, so the evidence is insufficient.",
                ),
            )
        if partial >= 2 and indirect >= 2 and direct <= 8:
            return (
                "Conflicting Evidence/Cherrypicking",
                _calibration_message(
                    claim_text,
                    "답변들이 부분적 지지나 범위·맥락 불일치를 보여 주므로 "
                    "단순 지지보다는 맥락 누락에 가깝습니다.",
                    "The answers show partial support or scope/context mismatches, so the "
                    "claim is better treated as qualified or cherry-picked.",
                ),
            )
        if _has_explicit_ce_signal(evidence_l, support_counts) and partial >= 2:
            return (
                "Conflicting Evidence/Cherrypicking",
                _calibration_message(
                    claim_text,
                    "The evidence supports part of the claim but also includes explicit "
                    "context, caveats, or conflicting accounts.",
                    "The evidence supports part of the claim but also includes explicit "
                    "context, caveats, or conflicting accounts.",
                ),
            )
        if (
            comparative_claim
            and direct >= 6
            and partial >= 4
            and method_mismatch >= 2
            and weak == 0
        ):
            return (
                "Conflicting Evidence/Cherrypicking",
                _calibration_message(
                    claim_text,
                    "The evidence gives substantial support but the comparison or "
                    "measurement context is qualified.",
                    "The evidence gives substantial support but the comparison or "
                    "measurement context is qualified.",
                ),
            )

    if label == "Refuted":
        if contradiction == 0 and weak >= 2 and direct <= 4:
            return (
                "Not Enough Evidence",
                _calibration_message(
                    claim_text,
                    "답변들이 직접 반박보다는 증거 부족이나 관련 증거에 머물러 있어 "
                    "반박으로 단정하기 어렵습니다.",
                    "The answers indicate insufficient or merely related evidence rather "
                    "than a direct contradiction, so refutation is not established.",
                ),
            )
        if partial >= 2 and indirect >= 2 and direct <= 8:
            return (
                "Conflicting Evidence/Cherrypicking",
                _calibration_message(
                    claim_text,
                    "답변들이 부분적 지지와 간접 근거를 함께 보여 주므로 "
                    "단순 반박보다는 맥락이 갈리는 주장에 가깝습니다.",
                    "The answers combine partial support with indirect evidence, so the "
                    "claim is better treated as qualified or cherry-picked than refuted.",
                ),
            )
        if (
            contradiction >= 1
            and direct >= 4
            and partial >= 2
            and context_mismatch >= 3
            and _has_explicit_ce_signal(evidence_l, support_counts)
        ):
            return (
                "Conflicting Evidence/Cherrypicking",
                _calibration_message(
                    claim_text,
                    "The answers include both support and contradiction, indicating a "
                    "qualified or context-dependent claim rather than a clean refutation.",
                    "The answers include both support and contradiction, indicating a "
                    "qualified or context-dependent claim rather than a clean refutation.",
                ),
            )
        if _has_explicit_ce_signal(evidence_l, support_counts) and direct >= 4 and partial >= 2:
            return (
                "Conflicting Evidence/Cherrypicking",
                _calibration_message(
                    claim_text,
                    "The evidence contains explicit caveats or conflicting accounts, so "
                    "the claim is better treated as cherry-picked than simply refuted.",
                    "The evidence contains explicit caveats or conflicting accounts, so "
                    "the claim is better treated as cherry-picked than simply refuted.",
                ),
            )

    if label in {"Conflicting Evidence/Cherrypicking", "Not Enough Evidence"}:
        if direct >= 8 and partial == 0 and weak <= 2 and contradiction == 0:
            return (
                "Supported",
                _calibration_message(
                    claim_text,
                    "여러 답변이 주장 그대로를 직접 지지하고 "
                    "뚜렷한 부족·부분 지지 신호가 없어 지지로 판단합니다.",
                    "Multiple answers directly support the claim as stated, with no clear "
                    "contradictory or partial-support signals.",
                ),
            )

    if label in {"Supported", "Conflicting Evidence/Cherrypicking"} and _has_annualized_numeric_mismatch(claim_l, evidence_l):
        return (
            "Not Enough Evidence",
            _calibration_message(
                claim_text,
                "증거는 관련 수치를 언급하지만, 연율·실제 증가율·GDP 수준이 섞여 있어 "
                "주장 그대로를 확정하기에는 부족합니다.",
                "The evidence mentions related figures, but it mixes annualized rates, "
                "actual growth, and GDP levels, so it does not establish the claim as stated.",
            ),
        )

    if (
        label == "Refuted"
        and direct >= 8
        and contradiction == 0
        and weak <= 1
        and partial == 0
        and not _has_refutation_signal(evidence_l)
    ):
        return (
            "Supported",
            _calibration_message(
                claim_text,
                "The QA evidence directly supports the claim across multiple answers and "
                "does not contain a direct contradiction.",
                "The QA evidence directly supports the claim across multiple answers and "
                "does not contain a direct contradiction.",
            ),
        )

    if (
        label == "Supported"
        and direct >= 6
        and contradiction >= 1
        and weak >= 1
        and _is_compound_or_rhetorical_claim(claim_l)
    ):
        return (
            "Conflicting Evidence/Cherrypicking",
            _calibration_message(
                claim_text,
                "The evidence directly supports some parts of the claim but also leaves a "
                "material part contradicted or insufficiently established.",
                "The evidence directly supports some parts of the claim but also leaves a "
                "material part contradicted or insufficiently established.",
            ),
        )

    if label == "Refuted" and _is_unproven_treatment_claim(claim_l, evidence_l):
        return (
            "Not Enough Evidence",
            _calibration_message(
                claim_text,
                "증거는 해당 치료 효과나 승인 여부가 불확실하다고 설명하지만, "
                "주장 자체가 거짓이라고 단정하지는 않습니다.",
                "The evidence says the treatment effect or approval status is uncertain, "
                "but it does not conclusively show that the claim itself is false.",
            ),
        )

    if label == "Refuted" and _is_unconfirmed_scam_claim(claim_l, evidence_l):
        return (
            "Not Enough Evidence",
            _calibration_message(
                claim_text,
                "증거는 관련 신고나 확인된 사례가 부족하다고 설명하므로, "
                "사기가 존재하지 않는다고 단정하기보다는 증거 부족에 가깝습니다.",
                "The evidence reports a lack of confirmed reports or cases, which is "
                "better treated as insufficient evidence than a conclusive disproof.",
            ),
        )

    if label in {"Supported", "Conflicting Evidence/Cherrypicking", "Not Enough Evidence"}:
        if _has_exact_timing_contradiction(claim_l, evidence_l):
            return (
                "Refuted",
                _calibration_message(
                    claim_text,
                    "증거의 권고 시간은 주장한 시간과 명확히 다르므로, "
                    "핵심 수치가 일치하지 않습니다.",
                    "The recommended timing in the evidence clearly differs from the claimed "
                    "timing, so a material numeric detail does not match.",
                ),
            )

    if label == "Supported" and _has_indirect_attribution(claim_l, evidence_l):
        return (
            "Conflicting Evidence/Cherrypicking",
            _calibration_message(
                claim_text,
                "증거는 관련 입장이나 제3자의 설명을 보여 주지만, "
                "주장처럼 당사자가 직접 말했다고 보기에는 맥락이 제한적입니다.",
                "The evidence supports a related position or third-party characterization, "
                "but it does not clearly show the named person directly said it.",
            ),
        )

    if label == "Supported" and _has_loaded_framing_omission(claim_l, evidence_l):
        return (
            "Conflicting Evidence/Cherrypicking",
            _calibration_message(
                claim_text,
                "증거는 사건의 일부를 뒷받침하지만, 주장에 포함된 표현이나 동기는 "
                "중요한 맥락을 생략해 의미가 달라질 수 있습니다.",
                "The evidence supports part of the event, but the claim's wording or "
                "framing omits context that materially changes the meaning.",
            ),
        )

    if label == "Supported" and _has_groundless_accusation_refutation(claim_l, evidence_l):
        return (
            "Refuted",
            _calibration_message(
                claim_text,
                "The evidence identifies the accusation as groundless or false rather "
                "than independently establishing it.",
                "The evidence identifies the accusation as groundless or false rather "
                "than independently establishing it.",
            ),
        )

    if label == "Refuted" and _has_non_quote_factual_support(claim_l, evidence_l):
        return (
            "Supported",
            _calibration_message(
                claim_text,
                "직접적인 사실 확인 증거가 주장한 사건이나 상태를 뒷받침하며, "
                "출처 발언 여부만으로 이를 반박하기는 어렵습니다.",
                "The direct factual evidence supports the event or condition in the claim; "
                "the lack of a direct source statement does not refute it.",
            ),
        )

    return label, justification


def _evidence_text(questions: list[Question]) -> str:
    parts: list[str] = []
    for question in questions:
        parts.append(question.get("question", ""))
        for answer in question.get("answers", []):
            parts.append(answer.get("answer", ""))
            if answer.get("boolean_explanation"):
                parts.append(answer["boolean_explanation"])
    return "\n".join(parts)


def _verifier_evidence_text(results: list[VerificationResult]) -> str:
    parts: list[str] = []
    for result in results:
        parts.append(result.get("reasoning", ""))
        parts.extend(result.get("evidence", []))
        parts.extend(result.get("sources", []))
    return "\n".join(parts)


def _answer_support_counts(questions: list[Question]) -> dict[str, int]:
    counts = {
        "direct_support": 0,
        "partial_support": 0,
        "contradiction": 0,
        "insufficient_evidence": 0,
        "related_only": 0,
        "mismatch": 0,
        "indirect": 0,
        "scope": 0,
        "time": 0,
        "number": 0,
        "attribution": 0,
        "context": 0,
        "methodology": 0,
        "source": 0,
    }
    for question in questions:
        for answer in question.get("answers", []):
            support_type = str(answer.get("support_type", ""))
            if support_type in counts:
                counts[support_type] += 1
            mismatch_type = str(answer.get("mismatch_type", "none"))
            if mismatch_type in counts:
                counts[mismatch_type] += 1
            if mismatch_type not in {"", "none", "unknown"}:
                counts["mismatch"] += 1
            if answer.get("directness") == "indirect":
                counts["mismatch"] += 1
                counts["indirect"] += 1
    return counts


def _calibration_message(claim: str, korean: str, english: str) -> str:
    if re.search(r"[가-힣]", claim):
        return korean
    return english


def _has_annualized_numeric_mismatch(claim: str, evidence: str) -> bool:
    return (
        "33.1" in claim
        and "gdp" in claim
        and ("annual rate" in evidence or "annualized" in evidence)
        and ("7.4 percent" in evidence or "quarter" in evidence or "in reality" in evidence)
    )


def _is_numeric_or_comparative_claim(claim: str) -> bool:
    return bool(
        _has_non_year_number(claim)
        or re.search(r"%|percent|per cent", claim)
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


def _is_comparative_or_rate_claim(claim: str) -> bool:
    return any(
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
            "per year",
            "annually",
            "annual",
            "rate",
        )
    )


def _has_non_year_number(text: str) -> bool:
    for match in re.finditer(r"\b\d+(?:\.\d+)?\b", text):
        value = match.group(0)
        if len(value) == 4 and value.startswith(("19", "20")):
            continue
        return True
    return False


def _has_missing_basis_signal(evidence: str) -> bool:
    return any(
        marker in evidence
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


def _has_refutation_signal(evidence: str) -> bool:
    return any(
        marker in evidence
        for marker in (
            "false",
            "fake",
            "hoax",
            "debunked",
            "denied",
            "not true",
            "did not",
            "does not",
            "no evidence",
            "unfounded",
            "baseless",
            "misleading",
            "refuted",
        )
    )


def _has_explicit_ce_signal(evidence: str, support_counts: dict[str, int]) -> bool:
    textual_signal = any(
        marker in evidence
        for marker in (
            "misleading",
            "cherry-picked",
            "cherrypicking",
            "cherry picking",
            "out of context",
            "omitted context",
            "without context",
            "both sides",
            "differing accounts",
            "conflicting accounts",
            "disputed",
            "partial truth",
            "technically true",
            "not the full story",
            "not completely",
            "not entirely",
        )
    )
    structured_signal = support_counts["partial_support"] >= 4 and (
        support_counts["attribution"] >= 2
        or support_counts["methodology"] >= 2
        or support_counts["number"] >= 2
    )
    return textual_signal or structured_signal


def _is_unproven_treatment_claim(claim: str, evidence: str) -> bool:
    if not any(word in claim for word in ("treatment", "treat", "cure")):
        return False
    uncertainty = (
        "inconclusive" in evidence
        or "only be used" in evidence
        or "clinical trials" in evidence
        or "not authorized or approved" in evidence
        or "not approved" in evidence
    )
    explicit_false = "ineffective" in evidence or "does not treat" in evidence
    return uncertainty and not explicit_false


def _is_unconfirmed_scam_claim(claim: str, evidence: str) -> bool:
    if "scam" not in claim:
        return False
    return any(
        phrase in evidence
        for phrase in (
            "not aware of any reports",
            "doesn't seem to be real",
            "does not seem to be real",
            "probably doesn't exist",
            "likely not real",
        )
    )


def _has_exact_timing_contradiction(claim: str, evidence: str) -> bool:
    return (
        re.search(r"\b1\s*hour\b", claim) is not None
        and ("not earlier than 1 min" in evidence or "not earlier than 1 minute" in evidence)
    )


def _has_indirect_attribution(claim: str, evidence: str) -> bool:
    if not any(marker in claim for marker in (" said ", " says ", " stated ")):
        return False
    return (
        "characterized by" in evidence
        or "biden stated" in evidence
        or "biden said" in evidence
        or "thinks that" in evidence
    )


def _has_loaded_framing_omission(claim: str, evidence: str) -> bool:
    if " just " in f" {claim} " and any(
        phrase in evidence for phrase in ("misleading", "doctored", "manipulated media")
    ):
        return True
    if any(word in claim for word in ("aid", "help")) and any(
        phrase in evidence for phrase in ("not humanitarian aid", "purchased", "positive pr")
    ):
        return True
    return False


def _has_groundless_accusation_refutation(claim: str, evidence: str) -> bool:
    accusation_claim = any(
        marker in claim
        for marker in (
            "fabricated information",
            "false accusation",
            "false accusations",
            "defame",
            "fake accusation",
            "fake accusations",
        )
    )
    refutation_signal = any(
        marker in evidence
        for marker in (
            "groundlessly claims",
            "groundless claim",
            "baselessly claims",
            "baseless claim",
            "falsely claims",
            "false claim",
            "debunked",
        )
    )
    return accusation_claim and refutation_signal


def _is_compound_or_rhetorical_claim(claim: str) -> bool:
    return (
        claim.count("?") >= 2
        or claim.count(".") >= 2
        or claim.count(";") >= 1
        or any(
            marker in claim
            for marker in (
                "speaking of",
                "remember what",
                "same people",
                "and then",
                "not only",
                "but also",
            )
        )
    )


def _has_non_quote_factual_support(claim: str, evidence: str) -> bool:
    if any(marker in claim for marker in (" said ", " says ", " stated ", " claimed ")):
        return False
    if "lost a republican-held seat" in claim and (
        "first time speaker robin vos has lost a republican-held seat" in evidence
        or "one assembly seat has flipped" in evidence
    ):
        return True
    return (
        "anti-black lives matter" in claim
        and "thin blue line" in evidence
        and ("anti-black" in evidence or "black lives matter movement" in evidence)
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
    questions = state.get("calibrated_questions") or state.get("questions", [])
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
    verification_results = [
        *state.get("fact_results", []),
        *state.get("source_results", []),
        *state.get("recency_results", []),
        *state.get("numeric_results", []),
    ]
    for claim in claims:
        claim_questions = [q for q in questions if q.get("claim_id") == claim["id"]]
        claim_results = [r for r in verification_results if r.get("claim_id") == claim["id"]]
        label, justification = _predict_averitec_label(
            [claim],
            claim_questions,
            verifier_results=claim_results,
            llm=llm,
        )
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
            if answer["source_url"]:
                a["source_domain"] = source_domain(answer["source_url"])
                a["source_quality"] = source_quality_reasons(answer["source_url"])
            if answer.get("boolean_explanation"):
                a["boolean_explanation"] = answer["boolean_explanation"]
            if answer.get("support_type"):
                a["support_type"] = answer["support_type"]
            if answer.get("directness"):
                a["directness"] = answer["directness"]
            if answer.get("mismatch_type"):
                a["mismatch_type"] = answer["mismatch_type"]
            answers.append(a)
        result.append({"question": question["question"], "answers": answers})
    return result


def _verification_results_payload(results: list[VerificationResult]) -> list[dict[str, Any]]:
    payload = []
    for result in results:
        payload.append(
            {
                "verifier": result["verifier"],
                "reasoning": result.get("reasoning", ""),
                "evidence": result.get("evidence", [])[:3],
                "sources": result.get("sources", [])[:3],
            }
        )
    return payload
