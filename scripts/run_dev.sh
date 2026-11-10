#!/usr/bin/env bash
# Start AgencyOS with auto-reload at http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/../backend"
../.venv/bin/python -m app.cli init-db
exec ../.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port "${PORT:-8000}"
