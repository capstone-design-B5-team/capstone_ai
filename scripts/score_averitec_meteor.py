"""Compute AVeriTeC-style HU-METEOR and veracity@threshold scores.

This mirrors the official evaluator's core behavior while accepting this
project's native prediction shape:
  {"label": "...", "questions": [{"question": ..., "answers": [...]}]}

The official prediction shape with {"pred_label": ..., "evidence": [...]} is
also accepted.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from nltk.translate.meteor_score import single_meteor_score

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
VERDICTS = [
    "Supported",
    "Refuted",
    "Not Enough Evidence",
    "Conflicting Evidence/Cherrypicking",
]
REPORTING_LEVELS = [0.1, 0.2, 0.25, 0.3, 0.4, 0.5]
MAX_QUESTIONS = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score AVeriTeC predictions with HU-METEOR.")
    parser.add_argument("--gold", required=True, help="Gold AVeriTeC JSON path")
    parser.add_argument("--predictions", required=True, help="Prediction JSON path")
    parser.add_argument("--output", default="", help="Optional markdown report path")
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def meteor(candidate: str, reference: str) -> float:
    candidate_tokens = tokenize(candidate)
    reference_tokens = tokenize(reference)
    if not candidate_tokens or not reference_tokens:
        return 0.0
    return single_meteor_score(reference_tokens, candidate_tokens)


def qa_strings(example: dict[str, Any], *, prediction: bool) -> list[str]:
    if prediction and "evidence" in example:
        return [
            f"{str(item.get('question', ''))} {str(item.get('answer', ''))}".strip()
            for item in example.get("evidence", [])[:MAX_QUESTIONS]
            if isinstance(item, dict)
        ]

    result: list[str] = []
    questions = example.get("questions", [])
    if not isinstance(questions, list):
        return result

    for question in questions[:MAX_QUESTIONS if prediction else len(questions)]:
        if not isinstance(question, dict):
            continue
        q_text = str(question.get("question", ""))
        answers = question.get("answers", [])
        if isinstance(answers, dict):
            answers = [answers]
        if not answers:
            result.append(f"{q_text} No answer could be found.")
            continue
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            answer_text = str(answer.get("answer", ""))
            if prediction and answer.get("answer_type") == "Boolean":
                explanation = str(answer.get("boolean_explanation", "")).strip()
                if explanation:
                    answer_text = f"{answer_text}. {explanation}"
            if not prediction and answer.get("answer_type") == "Boolean":
                explanation = str(answer.get("boolean_explanation", "")).strip()
                if explanation:
                    answer_text = f"{answer_text}. {explanation}"
            result.append(f"{q_text} {answer_text}".strip())
    return result


def hungarian_meteor_score(prediction: dict[str, Any], gold: dict[str, Any]) -> float:
    pred_strings = qa_strings(prediction, prediction=True)[:MAX_QUESTIONS]
    gold_strings = qa_strings(gold, prediction=False)
    if not pred_strings or not gold_strings:
        return 0.0

    scores = [
        [meteor(pred_text, gold_text) for gold_text in gold_strings]
        for pred_text in pred_strings
    ]
    return max_assignment_score(scores) / len(gold_strings)


def max_assignment_score(scores: list[list[float]]) -> float:
    if not scores or not scores[0]:
        return 0.0
    pred_count = len(scores)
    gold_count = len(scores[0])
    target_matches = min(pred_count, gold_count)
    states: dict[tuple[int, int], float] = {(0, 0): 0.0}

    for pred_index in range(pred_count):
        next_states = dict(states)
        for (mask, matched), total in states.items():
            if matched >= target_matches:
                continue
            for gold_index in range(gold_count):
                bit = 1 << gold_index
                if mask & bit:
                    continue
                key = (mask | bit, matched + 1)
                next_states[key] = max(
                    next_states.get(key, 0.0),
                    total + scores[pred_index][gold_index],
                )
        states = next_states

    return max(
        (total for (_mask, matched), total in states.items() if matched == target_matches),
        default=0.0,
    )


def pred_label(prediction: dict[str, Any]) -> str:
    return str(prediction.get("pred_label") or prediction.get("label") or "")


def eval_id(index: int) -> str:
    return f"AVT-DEV-{index:04d}"


def f1_by_label(rows: list[tuple[str, str]]) -> dict[str, float]:
    counts: dict[str, Counter[str]] = {label: Counter() for label in VERDICTS}
    for gold_label, predicted_label in rows:
        for label in VERDICTS:
            if gold_label == label and predicted_label == label:
                counts[label]["tp"] += 1
            elif gold_label != label and predicted_label == label:
                counts[label]["fp"] += 1
            elif gold_label == label and predicted_label != label:
                counts[label]["fn"] += 1

    result: dict[str, float] = {}
    for label, counter in counts.items():
        tp = counter["tp"]
        precision = tp / (tp + counter["fp"]) if tp + counter["fp"] else 0.0
        recall = tp / (tp + counter["fn"]) if tp + counter["fn"] else 0.0
        result[label] = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
    result["macro"] = sum(result.values()) / len(VERDICTS)
    result["acc"] = sum(g == p for g, p in rows) / len(rows) if rows else 0.0
    return result


def summarize(gold: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> str:
    pred_by_id = {str(pred.get("eval_id", "")): pred for pred in predictions}
    rows: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    for index, gold_item in enumerate(gold):
        pred = pred_by_id.get(eval_id(index))
        if pred is not None:
            rows.append((gold_item, pred, hungarian_meteor_score(pred, gold_item)))

    qau_score = sum(score for _gold, _pred, score in rows) / len(rows) if rows else 0.0
    label_rows = [(gold_item.get("label", ""), pred_label(pred)) for gold_item, pred, _ in rows]
    f1 = f1_by_label(label_rows)
    gate_counts = Counter(
        (
            pred_label(pred) == gold_item.get("label"),
            score > 0.25,
        )
        for gold_item, pred, score in rows
    )
    confusion = Counter(
        (str(gold_item.get("label", "")), pred_label(pred))
        for gold_item, pred, _score in rows
    )

    averitec_scores = []
    for level in REPORTING_LEVELS:
        values = [
            float(score > level and pred_label(pred) == gold_item.get("label"))
            for gold_item, pred, score in rows
        ]
        averitec_scores.append(sum(values) / len(values) if values else 0.0)

    by_type: dict[str, list[float]] = defaultdict(list)
    for gold_item, _pred, score in rows:
        gated_score = score if score > 0.25 else 0.0
        for claim_type in gold_item.get("claim_types", []):
            by_type[str(claim_type)].append(gated_score)

    lines = [
        "# AVeriTeC HU-METEOR Score",
        "",
        f"- evaluated: {len(rows)}/{len(gold)}",
        f"- question-answer score (HU-METEOR): {qau_score:.4f}",
        "",
        "## Veracity F1",
    ]
    for label in [*VERDICTS, "macro", "acc"]:
        lines.append(f"- {label}: {f1[label]:.4f}")

    lines.extend(["", "## AVeriTeC Scores"])
    for level, score in zip(REPORTING_LEVELS, averitec_scores, strict=True):
        lines.append(f"- Veracity scores (meteor @ {level}): {score:.4f}")

    lines.extend(["", "## Gate Breakdown @ 0.25"])
    gate_labels = [
        ((True, True), "label correct + QA pass"),
        ((True, False), "label correct + QA fail"),
        ((False, True), "label wrong + QA pass"),
        ((False, False), "label wrong + QA fail"),
    ]
    for key, label in gate_labels:
        count = gate_counts[key]
        ratio = count / len(rows) if rows else 0.0
        lines.append(f"- {label}: {count} ({ratio:.1%})")

    lines.extend(["", "## Confusion Matrix"])
    for gold_label in VERDICTS:
        parts = [
            f"{predicted_label}={confusion[(gold_label, predicted_label)]}"
            for predicted_label in VERDICTS
            if confusion[(gold_label, predicted_label)]
        ]
        lines.append(f"- {gold_label}: {', '.join(parts) if parts else 'none'}")

    lines.extend(["", "## By Type @ 0.25"])
    for claim_type, values in sorted(by_type.items()):
        lines.append(f"- {claim_type}: {sum(values) / len(values):.4f}")

    lines.extend(["", "## Failure Buckets @ 0.25"])
    failure_buckets = [
        ((True, False), "Label correct, QA below threshold"),
        ((False, True), "QA above threshold, label wrong"),
        ((False, False), "Both label and QA failed"),
    ]
    for key, title in failure_buckets:
        lines.append("")
        lines.append(f"### {title}")
        matching = [
            (idx, gold_item, pred, score)
            for idx, (gold_item, pred, score) in enumerate(rows)
            if (pred_label(pred) == gold_item.get("label"), score > 0.25) == key
        ]
        if not matching:
            lines.append("- none")
            continue
        for idx, gold_item, pred, score in matching:
            claim = str(gold_item.get("claim", "")).replace("\n", " ")
            lines.append(
                f"- {eval_id(idx)} score={score:.3f} "
                f"gold={gold_item.get('label', '')} pred={pred_label(pred)} "
                f"claim={claim[:140]}"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    gold = load_json(Path(args.gold))
    predictions = load_json(Path(args.predictions))
    if not isinstance(gold, list) or not isinstance(predictions, list):
        raise TypeError("gold and predictions must both be JSON lists")

    report = summarize(gold, predictions)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
