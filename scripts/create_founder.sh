#!/usr/bin/env bash
# Usage: scripts/create_founder.sh you@example.com "Your Name"
set -euo pipefail
cd "$(dirname "$0")/../backend"
if [ $# -lt 2 ]; then
  echo "Usage: $0 EMAIL \"FULL NAME\"" >&2
  exit 1
fi
exec ../.venv/bin/python -m app.cli create-user --email "$1" --name "$2" --role FOUNDER
