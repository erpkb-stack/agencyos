#!/usr/bin/env bash
# One-time local setup: virtualenv, dependencies, .env, database.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'

[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-dev.txt

if [ ! -f .env ]; then
  cp .env.example .env
  KEY=$(.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))")
  sed -i.bak "s|^SECRET_KEY=.*|SECRET_KEY=${KEY}|" .env && rm -f .env.bak
  echo "Created .env with a random SECRET_KEY."
fi

(cd backend && ../.venv/bin/python -m app.cli init-db)
echo "Setup complete. Next: create your founder login with scripts/create_founder.sh"
