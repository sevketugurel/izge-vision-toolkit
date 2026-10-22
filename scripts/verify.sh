#!/bin/sh
# Full verification: lint, unit, harness lint + tests, PostgreSQL 18 integration (+ migration round trip),
# frontend build and Playwright e2e against a disposable Compose stack.
# Uses its own Compose projects and volumes; never touches the development database.
set -eu
cd "$(dirname "$0")/.."
. ./scripts/verify_docker.sh
verify_docker_init

E2E_PORT="${IZGE_VERIFY_PORT:-18000}"
DB_PORT="${IZGE_TEST_DB_PORT:-55432}"
DB_PROJECT=izge-verify-db
E2E_PROJECT=izge-verify
export IZGE_TEST_DB_PORT="$DB_PORT"
TEST_DATABASE_URL="postgresql+psycopg://izge:izge-test@127.0.0.1:$DB_PORT/izge_test"
# Explicit template: macOS mktemp -d ignores TMPDIR, and the harness sandbox only allows writes under TMPDIR.
MEDIA_TMP="$(mktemp -d "${TMPDIR:-/tmp}/izge-verify.XXXXXX")"

step() { printf '\n== %s\n' "$1"; }
cleanup() {
  status=$?
  if [ "$VERIFY_DOCKER_AVAILABLE" = 1 ] && [ "${IZGE_VERIFY_KEEP:-0}" != 1 ]; then
    docker compose -f compose.test.yaml -p "$DB_PROJECT" down -v >/dev/null 2>&1 || true
    IZGE_PORT="$E2E_PORT" docker compose -p "$E2E_PROJECT" down -v >/dev/null 2>&1 || true
  fi
  rm -rf "$MEDIA_TMP"
  if [ "$status" != 0 ]; then
    printf '\nVERIFY: FAILED (exit %s)\n' "$status"
  elif [ -n "$VERIFY_SKIPPED" ]; then
    printf '\nVERIFY: PASSED (KISMİ — atlanan: %s)\n' "$VERIFY_SKIPPED"
  else
    printf '\nVERIFY: PASSED\n'
  fi
}
trap cleanup EXIT

step "Python bağımlılıkları (uv.lock, ml eki dahil)"
uv sync --frozen --extra ml

step "Lint (ruff)"
uv run ruff check backend scripts

step "Birim testleri"
uv run python -c 'import sys; print("VERIFY: Python stdout probe", flush=True); print("VERIFY: Python stderr probe", file=sys.stderr, flush=True)'
PYTHONUNBUFFERED=1 uv run pytest -q -p no:cacheprovider --ignore=backend/tests/test_integration.py \
  --ignore=backend/tests/test_feeding_integration.py

step "Harness birim testleri"
(cd harness && uv sync --frozen && uv run --frozen ruff check src tests \
  && uv run --frozen pytest -q -p no:cacheprovider)

step "PostgreSQL 18 (Compose) + migration gidiş-dönüş"
if verify_docker_step postgres; then
  docker compose -f compose.test.yaml -p "$DB_PROJECT" up -d --wait
  export DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=backend
  uv run alembic upgrade head
  uv run alembic downgrade base
  uv run python scripts/check_empty_schema.py
  uv run alembic upgrade head
  uv run alembic check
fi

step "Entegrasyon testleri (PostgreSQL 18)"
if verify_docker_step postgres-integration; then
  IZGE_TEST_DATABASE=1 MEDIA_ROOT="$MEDIA_TMP" uv run pytest -q -p no:cacheprovider backend/tests/test_integration.py \
    backend/tests/test_feeding_integration.py
  unset DATABASE_URL PYTHONPATH
fi

step "Frontend kurulum ve build"
npm ci --prefix frontend --no-audit --no-fund
npm run build --prefix frontend

step "Compose yığını (API + worker + PostgreSQL 18)"
if verify_docker_step compose; then
  # The e2e stack also loads the test-only synthetic camera config.
  export IZGE_CAMERA_CONFIG_DIRS=/app/config/cameras:/app/backend/tests/fixtures/cameras
  IZGE_PORT="$E2E_PORT" docker compose -p "$E2E_PROJECT" up -d --build --wait
  curl -fsS "http://127.0.0.1:$E2E_PORT/api/health"
  echo
  step "Sentetik demo tohumu (DEMO işaretli)"
  IZGE_PORT="$E2E_PORT" docker compose -p "$E2E_PROJECT" exec -T api python -m izge.demo
fi

step "Playwright e2e"
if verify_docker_step playwright-e2e; then
  (cd frontend && npx playwright install chromium >/dev/null && IZGE_E2E_URL="http://127.0.0.1:$E2E_PORT" npx playwright test)
fi
