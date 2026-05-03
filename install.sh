#!/usr/bin/env bash
# Set up a local Python environment for the Meraki reporting pipeline.

set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    echo "ERROR: python3 is required but was not found on PATH." >&2
    exit 1
  fi
fi

echo "Using Python: $("$PYTHON_BIN" --version 2>&1)"

if [[ ! -d ".venv" ]]; then
  echo "Creating .venv..."
  "$PYTHON_BIN" -m venv .venv
else
  echo "Using existing .venv."
fi

VENV_PYTHON=".venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "ERROR: .venv was created but .venv/bin/python is not executable." >&2
  exit 1
fi

echo "Installing Python dependencies..."
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r requirements.txt pytest

if [[ ! -f ".env" && -f ".env.example" ]]; then
  echo "Creating .env from .env.example. Add MERAKI_API_KEY before running a full API collection."
  cp .env.example .env
else
  echo "Leaving existing .env untouched."
fi

echo "Running test suite..."
"$VENV_PYTHON" -m pytest -q

echo ""
echo "Install complete."
echo "Run reports from existing backups: ./run.sh --report-only --no-ai-review --no-open"
echo "Run full collection: ./run.sh"
