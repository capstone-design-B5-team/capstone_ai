"""Calibrate QA answer support metadata before aggregation."""

from __future__ import annotations

from copy import deepcopy

from ai_backend.core.verification import answer_support_metadata
from ai_backend.graph.state import Claim, GraphState, Question


def evidence_calibration_node(state: GraphState) -> dict[str, list[Question]]:
    """Normalize answer-level support metadata for aggregate."""
    claims_by_id = {claim["id"]: claim for claim in state.get("claims", [])}
    fallback_claim = state["claims"][0] if state.get("claims") else None
    calibrated: list[Question] = []

    for question in state.get("questions", []):
        claim = claims_by_id.get(question.get("claim_id", ""), fallback_claim)
        if claim is None:
            calibrated.append(deepcopy(question))
            continue
        calibrated_question = deepcopy(question)
        for answer in calibrated_question.get("answers", []):
            metadata = answer_support_metadata(
                {
                    "support_type": answer.get("support_type", ""),
                    "directness": answer.get("directness", ""),
                    "mismatch_type": answer.get("mismatch_type", ""),
                },
                claim=_claim_text(claim),
                question=calibrated_question.get("question", ""),
                answer=answer.get("answer", ""),
                answer_type=answer.get("answer_type", ""),
                boolean_explanation=answer.get("boolean_explanation", ""),
                source_url=answer.get("source_url", ""),
            )
            answer.update(metadata)
        calibrated.append(calibrated_question)

    return {"calibrated_questions": calibrated}


def _claim_text(claim: Claim) -> str:
    return claim["text"]
