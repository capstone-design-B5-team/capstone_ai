"""Generate AVeriTeC predictions from a JSON dataset."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ai_backend.graph.builder import verification_graph
from ai_backend.graph.nodes.scoring_ranker import rank_scoring_questions


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
        "averitec_claim_types": item.get("claim_types", []),
        "claim_date": item.get("claim_date", ""),
        "document_citations": [],
        "claims": [],
        "questions": [],
        "calibrated_questions": [],
        "fact_results": [],
        "source_results": [],
        "recency_results": [],
        "numeric_results": [],
        "claim_labels": [],
    }


def prediction_from_state(
    item: dict[str, Any],
    index: int,
    state: dict[str, Any],
) -> dict[str, Any]:
    all_results = [
        *state.get("fact_results", []),
        *state.get("source_results", []),
        *state.get("recency_results", []),
        *state.get("numeric_results", []),
    ]
    node_results = [
        {
            "verifier": r["verifier"],
            "search_queries": r.get("metadata", {}).get("search_queries", []),
            "evidence": r.get("evidence", []),
            "sources": r.get("sources", []),
            "reasoning": r.get("reasoning", ""),
        }
        for r in all_results
    ]
    # AVeriTeC 모드에서는 claim이 1개이므로 claim_labels[0] 사용
    claim_labels = state.get("claim_labels", [])
    if claim_labels:
        label = claim_labels[0]["label"]
        justification = claim_labels[0]["justification"]
    else:
        label = "Not Enough Evidence"
        justification = ""

    questions = state.get("calibrated_questions") or state.get("questions", [])
    ranked_questions = rank_scoring_questions(
        questions,
        claim=str(item.get("claim", "")),
        label=label,
    )

    return {
        "eval_id": f"AVT-DEV-{index:04d}",
        "claim": item.get("claim", ""),
        "label": label,
        "questions": ranked_questions,
        "justification": justification,
        "claim_labels": claim_labels,
        "node_results": node_results,
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

    # 기존 출력 파일이 있으면 이어쓰기 (중단 후 재시작 지원)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    predictions: list[dict[str, Any]] = []
    done_ids: set[str] = set()
    if output_path.exists():
        try:
            with output_path.open("r", encoding="utf-8") as f:
                predictions = json.load(f)
            done_ids = {p["eval_id"] for p in predictions if p.get("eval_id")}
            print(
                f"Resume: {len(done_ids)} items already done, skipping.",
                file=sys.stderr,
            )
        except Exception:
            predictions = []

    total = len(selected)
    for offset, item in enumerate(selected):
        index = start + offset
        if not isinstance(item, dict):
            raise TypeError(f"item[{index}] must be an object")
        eid = f"AVT-DEV-{index:04d}"
        if eid in done_ids:
            print(
                f"[{offset + 1}/{total}] skip index={index} (already done)",
                file=sys.stderr,
                flush=True,
            )
            continue
        print(f"[{offset + 1}/{total}] claim index={index}", file=sys.stderr, flush=True)
        result = await verification_graph.ainvoke(initial_state(item, index))
        predictions.append(prediction_from_state(item, index, dict(result)))

        # 항목마다 즉시 저장 — 중단돼도 완료된 항목은 보존됨
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
