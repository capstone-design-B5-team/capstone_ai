"""Generate AVeriTeC predictions from a JSON dataset."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ai_backend.graph.builder import verification_graph


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the verification graph over an AVeriTeC JSON file.",
    )
    parser.add_argument("--input", required=True, help="AVeriTeC JSON input path")
    parser.add_argument(
        "--output",
        default="predictions.json",
        help="Predictions output path",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of claims to process",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Start index in the input file",
    )
    return parser.parse_args()


def initial_state(item: dict[str, Any], index: int) -> dict[str, Any]:
    claim = item.get("claim")
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError(f"item[{index}] does not contain a non-empty claim")

    return {
        "raw_text": claim,
        "document_id": str(item.get("id") or item.get("claim_id") or index),
        "run_mode": "averitec",
        "document_citations": [],
        "claims": [],
        "questions": [],
        "fact_results": [],
        "source_results": [],
        "recency_results": [],
        "numeric_results": [],
        "label": "Not Enough Evidence",
        "justification": "",
        "final_grade": "확인 필요",
        "final_report": {
            "final_grade": "확인 필요",
            "summary": "",
            "issues": [],
        },
    }


def prediction_from_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": state.get("label", "Not Enough Evidence"),
        "questions": state.get("questions", []),
        "justification": state.get("justification", ""),
    }


async def run_predictions(
    *,
    input_path: Path,
    output_path: Path,
    start: int,
    limit: int | None,
) -> None:
    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise TypeError("AVeriTeC input must be a JSON list")
    if start < 0:
        raise ValueError("--start must be non-negative")
    if limit is not None and limit < 0:
        raise ValueError("--limit must be non-negative")

    selected = data[start:] if limit is None else data[start : start + limit]
    predictions: list[dict[str, Any]] = []

    total = len(selected)
    for offset, item in enumerate(selected):
        index = start + offset
        if not isinstance(item, dict):
            raise TypeError(f"item[{index}] must be an object")
        print(f"[{offset + 1}/{total}] claim index={index}", file=sys.stderr, flush=True)
        result = await verification_graph.ainvoke(initial_state(item, index))
        predictions.append(prediction_from_state(dict(result)))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Wrote {len(predictions)} predictions to {output_path}", file=sys.stderr)


def main() -> None:
    args = parse_args()
    asyncio.run(
        run_predictions(
            input_path=Path(args.input),
            output_path=Path(args.output),
            start=args.start,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
