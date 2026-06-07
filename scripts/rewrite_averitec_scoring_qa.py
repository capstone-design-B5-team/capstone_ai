"""Rewrite AVeriTeC prediction QA for scorer-friendly evidence output."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from ai_backend.core.llm import get_llm
from ai_backend.graph.nodes.scoring_qa import rewrite_scoring_questions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite prediction questions into compact AVeriTeC scoring QA.",
    )
    parser.add_argument("--input", required=True, help="Source prediction JSON path")
    parser.add_argument("--output", required=True, help="Rewritten prediction JSON path")
    parser.add_argument("--gold", default="", help="Optional gold JSON for claim_date lookup")
    parser.add_argument("--start", type=int, default=0, help="Start index in prediction file")
    parser.add_argument("--limit", type=int, default=None, help="Maximum predictions to rewrite")
    parser.add_argument(
        "--preserve-original",
        type=int,
        default=9,
        help=(
            "Keep this many original QA items first, then fill remaining scorer "
            "slots with rewritten QA. Use 0 to fully replace questions."
        ),
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


async def rewrite_predictions(
    *,
    input_path: Path,
    output_path: Path,
    gold_path: Path | None,
    start: int,
    limit: int | None,
    preserve_original: int,
) -> None:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("--output must be different from --input")

    predictions = load_json(input_path)
    if not isinstance(predictions, list):
        raise TypeError("input predictions must be a JSON list")
    gold = load_json(gold_path) if gold_path else []
    if gold and not isinstance(gold, list):
        raise TypeError("gold must be a JSON list")

    selected = predictions[start:] if limit is None else predictions[start : start + limit]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rewritten_predictions: list[dict[str, Any]] = []
    done_ids: set[str] = set()
    if output_path.exists():
        try:
            existing = load_json(output_path)
            if isinstance(existing, list):
                rewritten_predictions = existing
                done_ids = {
                    str(item.get("eval_id", ""))
                    for item in existing
                    if isinstance(item, dict) and item.get("eval_id")
                }
                print(
                    f"Resume: {len(done_ids)} items already done, skipping.",
                    file=sys.stderr,
                )
        except Exception:
            rewritten_predictions = []
            done_ids = set()

    llm = get_llm("aggregation")
    total = len(selected)
    for offset, prediction in enumerate(selected):
        index = start + offset
        if not isinstance(prediction, dict):
            raise TypeError(f"prediction[{index}] must be an object")
        eval_id = str(prediction.get("eval_id") or f"AVT-DEV-{index:04d}")
        if eval_id in done_ids:
            print(
                f"[{offset + 1}/{total}] skip {eval_id} (already done)",
                file=sys.stderr,
                flush=True,
            )
            continue

        print(f"[{offset + 1}/{total}] rewrite {eval_id}", file=sys.stderr, flush=True)
        rewritten = dict(prediction)
        original_questions = prediction.get("questions", [])
        if not isinstance(original_questions, list):
            original_questions = []
        rewritten_questions = rewrite_scoring_questions(
            claim_text=str(prediction.get("claim", "")),
            label=str(prediction.get("label") or prediction.get("pred_label") or ""),
            claim_date=_claim_date_for(gold, eval_id, index),
            questions=original_questions,
            llm=llm,
            claim_id=eval_id,
        )
        if rewritten_questions:
            rewritten["questions"] = _merge_questions(
                original_questions,
                rewritten_questions,
                preserve_original=preserve_original,
                max_questions=10,
            )
        else:
            rewritten["questions"] = original_questions[:10]
            rewritten["scoring_qa_rewrite_error"] = True

        rewritten_predictions.append(rewritten)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(rewritten_predictions, f, ensure_ascii=False, indent=2)
            f.write("\n")

    print(f"Wrote {len(rewritten_predictions)} predictions to {output_path}", file=sys.stderr)


def _claim_date_for(gold: list[Any], eval_id: str, fallback_index: int) -> str:
    index = fallback_index
    match = re.search(r"(\d+)$", eval_id)
    if match:
        index = int(match.group(1))
    if 0 <= index < len(gold) and isinstance(gold[index], dict):
        return str(gold[index].get("claim_date", ""))
    return ""


def _merge_questions(
    original_questions: list[Any],
    rewritten_questions: list[Any],
    *,
    preserve_original: int,
    max_questions: int,
) -> list[Any]:
    """Conservatively add rewritten QA without discarding strong original QA."""
    merged: list[Any] = []
    seen: set[str] = set()

    for question in original_questions[: max(0, preserve_original)]:
        key = _question_key(question)
        if key in seen:
            continue
        merged.append(question)
        seen.add(key)
        if len(merged) >= max_questions:
            return merged

    for question in rewritten_questions:
        key = _question_key(question)
        if key in seen:
            continue
        merged.append(question)
        seen.add(key)
        if len(merged) >= max_questions:
            return merged

    for question in original_questions:
        key = _question_key(question)
        if key in seen:
            continue
        merged.append(question)
        seen.add(key)
        if len(merged) >= max_questions:
            break
    return merged


def _question_key(question: Any) -> str:
    if not isinstance(question, dict):
        return str(question)
    answers = question.get("answers", [])
    if isinstance(answers, dict):
        answers = [answers]
    answer_text = " ".join(
        str(answer.get("answer", ""))
        for answer in answers
        if isinstance(answer, dict)
    )
    text = f"{question.get('question', '')} {answer_text}".lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()[:220]


def main() -> None:
    args = parse_args()
    asyncio.run(
        rewrite_predictions(
            input_path=Path(args.input),
            output_path=Path(args.output),
            gold_path=Path(args.gold) if args.gold else None,
            start=args.start,
            limit=args.limit,
            preserve_original=args.preserve_original,
        )
    )


if __name__ == "__main__":
    main()
