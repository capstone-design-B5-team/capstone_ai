#!/usr/bin/env bash
# Configure this file, then run:
#   bash scripts/run_test.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# ---- AVeriTeC test settings ----
INPUT_JSON="scripts/averitec_dev_gold.json"
OUTPUT_JSON="scripts/predictions.json"
START=0
LIMIT=5

# Set LIMIT="" to process every item from START.
# Example:
#   LIMIT=""

if [[ -x ".venv/Scripts/python.exe" ]]; then
  PYTHON=".venv/Scripts/python.exe"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python"
fi

export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

ARGS=(
  --input "$INPUT_JSON"
  --output "$OUTPUT_JSON"
  --start "$START"
)

if [[ -n "$LIMIT" ]]; then
  ARGS+=(--limit "$LIMIT")
fi

exec "$PYTHON" scripts/run_averitec_predictions.py "${ARGS[@]}"
