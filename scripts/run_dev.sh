#!/usr/bin/env bash
# Run the local FastAPI development server.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x ".venv/Scripts/python.exe" ]]; then
  PYTHON=".venv/Scripts/python.exe"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python"
fi

exec "$PYTHON" -m uvicorn ai_backend.main:app --reload --host 0.0.0.0 --port 8000
