"""Summarize AVeriTeC experiment predictions for quick iteration.

This is not the official AVeriTeC scorer. It reports stable diagnostics that
help compare local experiments before running the full official evaluation.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize AVeriTeC predictions.")
    parser.add_argument("--gold", required=True, help="Gold AVeriTeC JSON path")
    parser.add_argument("--predictions", required=True, help="Prediction JSON path")
    parser.add_argument("--output", default="", help="Optional markdown report path")
    parser.add_argument("--top-errors", type=int, default=10, help="Number of label errors to list")
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def eval_id(index: int) -> str:
    return f"AVT-DEV-{index:04d}"


def tokens(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text)}


def token_f1(a: str, b: str) -> float:
    left = tokens(a)
    right = tokens(b)
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    if overlap == 0:
        return 0.0
    precision = overlap / len(left)
    recall = overlap / len(right)
    return 2 * precision * recall / (precision + recall)


def qa_similarity(
    pred_questions: list[dict[str, Any]],
    gold_questions: list[dict[str, Any]],
) -> float:
    if not pred_questions or not gold_questions:
        return 0.0
    scores: list[float] = []
    for pred in pred_questions:
        pred_text = str(pred.get("question", ""))
        best = max(
            (token_f1(pred_text, str(gold.get("question", ""))) for gold in gold_questions),
            default=0.0,
        )
        scores.append(best)
    return sum(scores) / len(scores) if scores else 0.0


def answer_texts(question: dict[str, Any]) -> list[str]:
    answers = question.get("answers", [])
    if not isinstance(answers, list):
        return []
    return [str(answer.get("answer", "")) for answer in answers if isinstance(answer, dict)]


def answer_similarity(
    pred_questions: list[dict[str, Any]],
    gold_questions: list[dict[str, Any]],
) -> float:
    pred_answers = [text for question in pred_questions for text in answer_texts(question)]
    gold_answers = [text for question in gold_questions for text in answer_texts(question)]
    if not pred_answers or not gold_answers:
        return 0.0
    scores: list[float] = []
    for pred in pred_answers:
        scores.append(max((token_f1(pred, gold) for gold in gold_answers), default=0.0))
    return sum(scores) / len(scores) if scores else 0.0


def answer_type_counts(predictions: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for prediction in predictions:
        for question in prediction.get("questions", []):
            for answer in question.get("answers", []):
                if isinstance(answer, dict):
                    counts[str(answer.get("answer_type", "(missing)"))] += 1
    return counts


def average_answer_words(predictions: list[dict[str, Any]]) -> float:
    lengths: list[int] = []
    for prediction in predictions:
        for question in prediction.get("questions", []):
            for answer in question.get("answers", []):
                if isinstance(answer, dict):
                    lengths.append(len(str(answer.get("answer", "")).split()))
    return sum(lengths) / len(lengths) if lengths else 0.0


def summarize(
    gold: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    top_errors: int,
) -> str:
    pred_by_id = {str(pred.get("eval_id", "")): pred for pred in predictions}
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(gold):
        pred = pred_by_id.get(eval_id(index))
        if pred is None:
            continue
        rows.append({"index": index, "gold": item, "pred": pred})

    total = len(rows)
    correct = sum(1 for row in rows if row["gold"].get("label") == row["pred"].get("label"))
    label_accuracy = correct / total if total else 0.0

    by_type: dict[str, list[bool]] = defaultdict(list)
    by_label: dict[str, list[bool]] = defaultdict(list)
    q_scores: list[float] = []
    a_scores: list[float] = []
    question_counts: list[int] = []
    errors: list[dict[str, Any]] = []

    for row in rows:
        gold_item = row["gold"]
        pred = row["pred"]
        is_correct = gold_item.get("label") == pred.get("label")
        for claim_type in gold_item.get("claim_types", []) or ["(missing)"]:
            by_type[str(claim_type)].append(is_correct)
        by_label[str(gold_item.get("label", "(missing)"))].append(is_correct)

        pred_questions = pred.get("questions", [])
        gold_questions = gold_item.get("questions", [])
        question_counts.append(len(pred_questions) if isinstance(pred_questions, list) else 0)
        q_scores.append(qa_similarity(pred_questions, gold_questions))
        a_scores.append(answer_similarity(pred_questions, gold_questions))

        if not is_correct:
            errors.append(row)

    lines: list[str] = []
    lines.append("# AVeriTeC Experiment Summary")
    lines.append("")
    lines.append(f"- evaluated: {total}/{len(gold)}")
    lines.append(f"- label accuracy: {label_accuracy:.4f} ({correct}/{total})")
    lines.append(f"- avg predicted questions: {sum(question_counts) / total if total else 0.0:.2f}")
    lines.append(f"- avg question token-F1 proxy: {sum(q_scores) / total if total else 0.0:.4f}")
    lines.append(f"- avg answer token-F1 proxy: {sum(a_scores) / total if total else 0.0:.4f}")
    lines.append(f"- avg answer words: {average_answer_words(predictions):.2f}")
    lines.append("")

    lines.append("## Answer Types")
    type_counts = answer_type_counts(predictions)
    answer_total = sum(type_counts.values())
    for answer_type, count in sorted(type_counts.items()):
        ratio = count / answer_total if answer_total else 0.0
        lines.append(f"- {answer_type}: {count} ({ratio:.1%})")
    lines.append("")

    lines.append("## Accuracy By Claim Type")
    for claim_type, values in sorted(by_type.items()):
        acc = sum(values) / len(values) if values else 0.0
        lines.append(f"- {claim_type}: {acc:.4f} ({sum(values)}/{len(values)})")
    lines.append("")

    lines.append("## Accuracy By Gold Label")
    for label, values in sorted(by_label.items()):
        acc = sum(values) / len(values) if values else 0.0
        lines.append(f"- {label}: {acc:.4f} ({sum(values)}/{len(values)})")
    lines.append("")

    lines.append(f"## First {min(top_errors, len(errors))} Label Errors")
    if not errors:
        lines.append("- none")
    for row in errors[:top_errors]:
        gold_item = row["gold"]
        pred = row["pred"]
        lines.append("")
        lines.append(f"### {eval_id(row['index'])}")
        lines.append(f"- claim: {gold_item.get('claim', '')}")
        lines.append(f"- type: {', '.join(gold_item.get('claim_types', []) or [])}")
        lines.append(f"- gold: {gold_item.get('label', '')}")
        lines.append(f"- predicted: {pred.get('label', '')}")
        lines.append(f"- predicted questions: {len(pred.get('questions', []))}")
        lines.append("- suggested tag: TODO")

    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    gold = load_json(Path(args.gold))
    predictions = load_json(Path(args.predictions))
    if not isinstance(gold, list):
        raise TypeError("gold must be a JSON list")
    if not isinstance(predictions, list):
        raise TypeError("predictions must be a JSON list")

    report = summarize(gold, predictions, args.top_errors)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
