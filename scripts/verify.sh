#!/bin/sh
set -eu
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -p test_domain.py -v
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -p test_identity.py -v
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -p test_video_tools.py -v
npm ci --prefix frontend
npm run build --prefix frontend
npm run smoke --prefix frontend
# Use a disposable PostgreSQL 18 database, never a farm database.
: "${DATABASE_URL:?Set a disposable PostgreSQL 18 DATABASE_URL}"
uv sync --locked
IZGE_TEST_DATABASE=1 uv run pytest backend/tests/test_integration.py -v
