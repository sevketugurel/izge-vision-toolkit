#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
[[ "$(uname -s)" == Darwin ]] || { echo 'Bu kurulum native macOS içindir.'; exit 1; }
command -v uv >/dev/null || { echo 'Önce uv kurun: https://docs.astral.sh/uv/'; exit 1; }
uv sync --frozen --extra training --extra ml
uv run --extra training --extra ml python -c 'import torch; print({"mps_available": torch.backends.mps.is_available()}); assert torch.backends.mps.is_available(), "Native Apple Silicon/MPS gerekli"'
export PYTHONPATH="$PWD/backend"
export IZGE_RUNS_ROOT="${IZGE_RUNS_ROOT:-$HOME/.izge-runner/runs}"
uv run --extra training --extra ml python -m izge.training.runner &
runner_pid=$!
trap 'kill "$runner_pid" 2>/dev/null || true' EXIT
for attempt in {1..30}; do
  if IZGE_RUNNER_URL=http://127.0.0.1:8765 IZGE_RUNNER_TOKEN_FILE="${IZGE_RUNNER_TOKEN_FILE:-$HOME/.izge-runner/token}" uv run --extra training --extra ml python -m izge.training.client health; then
    wait "$runner_pid"
    exit
  fi
  sleep 1
done
exit 1
