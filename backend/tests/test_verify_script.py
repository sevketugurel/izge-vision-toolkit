"""Exercise the real verification flow with isolated executables; never use Docker."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SKIPPED = ("postgres", "postgres-integration", "compose", "playwright-e2e")


def run_verify(tmp_path, docker_status=None, require=False, docker_host="", fail_command="",
               node_status=0, require_node=False, node_version="v22.18.0",
               node_versions=None, runtime_status=0, frontend_statuses=(0,),
               frontend_output="", frontend_stderr=False, frontend_step="run",
               frontend_retry_output=None, harness=True):
    for directory in ("scripts", "harness", "frontend", "bin") if harness else ("scripts", "frontend", "bin"):
        (tmp_path / directory).mkdir()
    for name in ("verify.sh", "verify_docker.sh"):
        shutil.copyfile(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    # An isolated PATH guarantees that the missing-docker case cannot find a host daemon.
    for name in ("dirname", "mktemp", "rm", "sed", "head", "grep"):
        (tmp_path / "bin" / name).symlink_to(shutil.which(name))
    log = tmp_path / "calls.log"
    frontend_cases = "".join(
        f"{index}) exit {status} ;;\n"
        for index, status in enumerate(frontend_statuses, 1)
    )
    frontend_fake = (
        'if [ "$1" = --frontend-command ]; then\n'
        '  count=0\n'
        '  if [ -f "$CALL_LOG.frontend-count" ]; then '
        'read -r count < "$CALL_LOG.frontend-count"; fi\n'
        '  count=$((count + 1))\n'
        '  printf \'%s\\n\' "$count" > "$CALL_LOG.frontend-count"\n'
        '  if [ "$count" = 2 ] && [ "$FRONTEND_RETRY_OVERRIDE" = 1 ]; then\n'
        '    FRONTEND_OUTPUT=$FRONTEND_RETRY_OUTPUT\n'
        '  fi\n'
        '  if [ "$FRONTEND_STDERR" = 1 ]; then\n'
        '    printf \'%s\' "$FRONTEND_OUTPUT" >&2\n'
        '  else printf \'%s\' "$FRONTEND_OUTPUT"; fi\n'
        f'  case "$count" in\n{frontend_cases}  esac\n'
        f'  exit {frontend_statuses[-1]}\n'
        'fi\n'
    )
    node = tmp_path / "bin" / "node"
    if node_status is not None:
        versions = node_versions or [(node_status, node_version)] * 3
        cases = "".join(
            f"{index}) printf '%s' '{version}'; exit {status} ;;\n"
            for index, (status, version) in enumerate(versions, 1)
        )
        node.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "node $*" >> "$CALL_LOG"\n'
            + frontend_fake
            + 'if [ "$1" = --version ]; then\n'
            '  count=0\n'
            '  if [ -f "$CALL_LOG.count" ]; then read -r count < "$CALL_LOG.count"; fi\n'
            '  count=$((count + 1))\n'
            '  printf \'%s\\n\' "$count" > "$CALL_LOG.count"\n'
            f'  case "$count" in\n{cases}  esac\n'
            'fi\n'
            f"exit {runtime_status}\n"
        )
        node.chmod(0o755)
    for name in ("uv", "npm", "npx", "curl", "docker", "sleep"):
        if name == "docker" and docker_status is None:
            continue
        executable = tmp_path / "bin" / name
        executable.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"{name} $*\" >> \"$CALL_LOG\"\n"
            'if [ "$1" = info ]; then\n'
            '  printf "host=%s\\n" "${DOCKER_HOST:-}" >> "$CALL_LOG"\n'
            f"  exit {docker_status or 0}\n"
            "fi\n"
            f'if [ "{name} $*" = "$FAIL_COMMAND" ]; then exit 23; fi\n'
            f'if [ "{name} $1" = "npm $FRONTEND_STEP" ]; then '
            'exec node --frontend-command "$@"; fi\n'
        )
        executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tmp_path / "bin"),
        "CALL_LOG": str(log),
        "FAIL_COMMAND": fail_command,
        "FRONTEND_STEP": frontend_step,
        "FRONTEND_OUTPUT": frontend_output,
        "FRONTEND_RETRY_OUTPUT": frontend_retry_output or "",
        "FRONTEND_RETRY_OVERRIDE": "1" if frontend_retry_output is not None else "0",
        "FRONTEND_STDERR": "1" if frontend_stderr else "0",
        "IZGE_VERIFY_REQUIRE_DOCKER": "1" if require else "0",
        "IZGE_VERIFY_REQUIRE_NODE": "1" if require_node else "0",
        "IZGE_VERIFY_KEEP": "0",
        "DOCKER_HOST": docker_host,
        "TMPDIR": str(tmp_path),
    }
    result = subprocess.run(
        ["/bin/sh", str(tmp_path / "scripts" / "verify.sh")],
        env=env, capture_output=True, text=True, timeout=15,
    )
    assert not list(tmp_path.glob("izge-verify.*")), "temporary media must be cleaned up"
    return result, log.read_text() if log.exists() else ""


def test_missing_harness_directory_is_skipped_not_fatal(tmp_path):
    # The public release ships without harness/; verify.sh must still run every other step.
    result, calls = run_verify(tmp_path, None, harness=False)
    assert result.returncode == 0, result.stderr
    assert "SKIP: harness (dizin yok)" in result.stdout
    assert "uv run --frozen ruff check src tests\n" not in calls
    assert "uv run ruff check backend scripts\n" in calls
    assert "npm run build --prefix frontend\n" in calls


@pytest.mark.parametrize("docker_status", [None, 1])
def test_unavailable_docker_skips_only_docker_steps(tmp_path, docker_status):
    result, calls = run_verify(tmp_path, docker_status)
    assert result.returncode == 0, result.stderr
    for label in SKIPPED:
        assert f"SKIP: {label} (docker yok — sandbox)" in result.stdout
    assert f"VERIFY: PASSED (KISMİ — atlanan: {', '.join(SKIPPED)})" in result.stdout
    assert "\nVERIFY: PASSED\n" not in result.stdout
    assert "docker compose" not in calls
    assert "alembic" not in calls
    assert "test_integration.py backend/tests/test_feeding_integration.py" not in calls
    assert "npx" not in calls
    assert "curl" not in calls
    assert calls.count("docker info\n") == (0 if docker_status is None else 1)
    # All independent checks still run, including the harness commands in its cwd.
    assert "uv sync --frozen --extra ml\n" in calls
    assert "uv run ruff check backend scripts\n" in calls
    assert "uv run pytest -q -p no:cacheprovider --ignore=" in calls
    assert "uv sync --frozen\n" in calls
    assert "uv run --frozen ruff check src tests\n" in calls
    assert "uv run --frozen pytest -q -p no:cacheprovider\n" in calls
    assert "npm ci --prefix frontend --no-audit --no-fund\n" in calls
    assert "npm run build --prefix frontend\n" in calls


@pytest.mark.parametrize("docker_host", ["", "tcp://test-docker:2375"])
@pytest.mark.parametrize("require", [False, True])
def test_available_docker_runs_all_steps(tmp_path, docker_host, require):
    result, calls = run_verify(tmp_path, 0, require=require, docker_host=docker_host)
    assert result.returncode == 0, result.stderr
    assert calls.count("docker info\n") == 1
    assert f"host={docker_host}\n" in calls
    assert "SKIP:" not in result.stdout
    assert "\nVERIFY: PASSED\n" in result.stdout
    assert "-p izge-verify-db up -d --wait\n" in calls
    assert "uv run alembic downgrade base\n" in calls
    assert calls.count("uv run alembic upgrade head\n") == 2
    assert "uv run python scripts/check_empty_schema.py\n" in calls
    assert "uv run alembic check\n" in calls
    assert "backend/tests/test_integration.py backend/tests/test_feeding_integration.py\n" in calls
    assert "-p izge-verify up -d --build --wait\n" in calls
    assert "curl -fsS http://127.0.0.1:" in calls
    assert "exec -T api python -m izge.demo\n" in calls
    assert "npx playwright install chromium\n" in calls
    assert "npx playwright test\n" in calls
    assert calls.count(" down -v\n") == 2


@pytest.mark.parametrize("docker_status", [None, 1])
def test_required_docker_fails_when_unavailable(tmp_path, docker_status):
    result, calls = run_verify(tmp_path, docker_status, require=True)
    assert result.returncode != 0
    assert "ERROR: Docker erişilemiyor; IZGE_VERIFY_REQUIRE_DOCKER=1" in result.stderr
    assert "SKIP:" not in result.stdout
    assert "VERIFY: PASSED" not in result.stdout
    assert "docker compose" not in calls


@pytest.mark.parametrize("docker_status", [None, 0])
def test_independent_step_failure_is_still_fatal(tmp_path, docker_status):
    result, _ = run_verify(tmp_path, docker_status, fail_command="npm run build --prefix frontend")
    assert result.returncode == 23
    assert "VERIFY: FAILED (exit 23)" in result.stdout
    assert "VERIFY: PASSED" not in result.stdout


def test_docker_step_failure_is_still_fatal(tmp_path):
    result, calls = run_verify(
        tmp_path, 0, fail_command="docker compose -f compose.test.yaml -p izge-verify-db up -d --wait",
    )
    assert result.returncode == 23
    assert "VERIFY: FAILED (exit 23)" in result.stdout
    assert "uv run alembic" not in calls
    assert "SKIP:" not in result.stdout


@pytest.mark.parametrize("node_status", [134, 1])
def test_aborting_node_skips_frontend_and_e2e(tmp_path, node_status):
    result, calls = run_verify(tmp_path, 0, node_status=node_status)
    assert result.returncode == 0, result.stderr
    suffix = " — 3 deneme" if node_status == 134 else ""
    assert f"SKIP: frontend (node sandbox'ta başlatılamadı{suffix})" in result.stdout
    assert f"SKIP: playwright-e2e (node sandbox'ta başlatılamadı{suffix})" in result.stdout
    assert "VERIFY: PASSED (KISMİ — atlanan: frontend, playwright-e2e)" in result.stdout
    assert "\nVERIFY: PASSED\n" not in result.stdout
    assert "npm" not in calls
    assert "uv run ruff check backend scripts\n" in calls
    assert "npx" not in calls
    attempts = 3 if node_status == 134 else 1
    assert calls.count("node --version\n") == attempts
    assert calls.count("sleep 2\n") == attempts - 1
    for attempt in range(1, attempts + 1):
        assert f"VERIFY: node --version deneme {attempt}/3 (exit {node_status})" in result.stdout
    assert "-p izge-verify up -d --build --wait\n" in calls


def test_aborting_node_and_missing_docker_list_both_skips(tmp_path):
    result, _ = run_verify(tmp_path, None, node_status=134)
    assert result.returncode == 0, result.stderr
    assert (
        "VERIFY: PASSED (KISMİ — atlanan: postgres, postgres-integration, frontend, compose, playwright-e2e)"
        in result.stdout
    )


@pytest.mark.parametrize("require_node", [False, True])
@pytest.mark.parametrize("node_version", ["v20.0.0", "v22.18.0", "v200.0.0"])
def test_healthy_node_runs_frontend_step(tmp_path, require_node, node_version):
    result, calls = run_verify(tmp_path, 0, require_node=require_node, node_version=node_version)
    assert result.returncode == 0, result.stderr
    assert calls.count("node -p 1\n") == 1
    assert calls.count("node --version\n") == 1
    assert "sleep" not in calls
    assert "SKIP:" not in result.stdout
    assert "\nVERIFY: PASSED\n" in result.stdout
    assert "npm ci --prefix frontend --no-audit --no-fund\n" in calls
    assert "npm run build --prefix frontend\n" in calls


def test_required_node_fails_when_probe_fails(tmp_path):
    result, calls = run_verify(tmp_path, 0, node_status=134, require_node=True)
    assert result.returncode != 0
    assert "ERROR: node başlatılamadı; IZGE_VERIFY_REQUIRE_NODE=1" in result.stderr
    assert "SKIP:" not in result.stdout
    assert "VERIFY: PASSED" not in result.stdout
    assert "npm" not in calls


@pytest.mark.parametrize("node_status", [None, 127])
def test_missing_node_skips_without_retry(tmp_path, node_status):
    result, calls = run_verify(tmp_path, 0, node_status=node_status)
    assert result.returncode == 0, result.stderr
    assert "VERIFY: node --version deneme 1/3 (exit 127)" in result.stdout
    assert "deneme 2/3" not in result.stdout
    assert "sleep" not in calls
    assert "SKIP: frontend (node sandbox'ta başlatılamadı)" in result.stdout
    assert "VERIFY: PASSED (KISMİ — atlanan: frontend, playwright-e2e)" in result.stdout
    assert "npm" not in calls
    assert "npx" not in calls


def test_empty_version_retries_then_skips(tmp_path):
    result, calls = run_verify(tmp_path, 0, node_version="")
    assert result.returncode == 0, result.stderr
    assert calls.count("node --version\n") == 3
    assert calls.count("sleep 2\n") == 2
    assert "node -p 1" not in calls
    for attempt in range(1, 4):
        assert f"VERIFY: node --version deneme {attempt}/3 (exit 0)" in result.stdout
    assert "SKIP: frontend (node sandbox'ta başlatılamadı — 3 deneme)" in result.stdout
    assert "VERIFY: PASSED (KISMİ" in result.stdout


def test_transient_abort_recovers_and_builds(tmp_path):
    result, calls = run_verify(
        tmp_path, 0, node_versions=[(134, ""), (0, "v22.18.0")],
    )
    assert result.returncode == 0, result.stderr
    assert calls.count("node --version\n") == 2
    assert calls.count("sleep 2\n") == 1
    assert calls.count("node -p 1\n") == 1
    assert "npm run build --prefix frontend\n" in calls
    assert "\nVERIFY: PASSED\n" in result.stdout


def test_old_node_fails_with_clear_error_before_build(tmp_path):
    result, calls = run_verify(tmp_path, 0, node_version="v18.20.8")
    assert result.returncode == 1
    assert result.stderr.splitlines() == [
        "ERROR: node v18 < 20; npm PATH'i nvm node 22'yi göstermeli",
    ]
    assert "npm" not in calls
    assert "node -p 1" not in calls
    assert "SKIP:" not in result.stdout
    assert "VERIFY: PASSED" not in result.stdout


def test_runtime_probe_failure_is_not_retried(tmp_path):
    result, calls = run_verify(tmp_path, 0, runtime_status=134)
    assert result.returncode == 0, result.stderr
    assert calls.count("node --version\n") == 1
    assert calls.count("node -p 1\n") == 1
    assert "sleep" not in calls
    assert "SKIP: frontend (node sandbox'ta başlatılamadı)" in result.stdout
    assert "npm" not in calls


@pytest.mark.parametrize("frontend_step", ["ci", "run"])
@pytest.mark.parametrize("docker_status", [0, 1])
def test_silent_real_command_abort_retries_then_skips(tmp_path, frontend_step, docker_status):
    result, calls = run_verify(
        tmp_path, docker_status=docker_status, frontend_step=frontend_step,
        frontend_statuses=(134,),
    )
    assert result.returncode == 0, result.stderr
    assert calls.count("node --frontend-command") == 2
    assert result.stdout.count("yeniden deneme 1/1") == 1
    assert "SKIP: frontend (node sandbox'ta başlatılamadı — gerçek komut 134)" in result.stdout
    skipped = (
        "frontend, playwright-e2e" if docker_status == 0 else
        "postgres, postgres-integration, frontend, compose, playwright-e2e"
    )
    assert f"VERIFY: PASSED (KISMİ — atlanan: {skipped})" in result.stdout
    assert "FAIL:" not in result.stderr
    assert "npx" not in calls
    if frontend_step == "ci":
        assert "npm run build --prefix frontend" not in calls
    diagnostic = tmp_path / "verify-node-crash.txt"
    assert diagnostic.is_file()
    assert "node -v" in diagnostic.read_text()
    assert str(diagnostic) in result.stdout


@pytest.mark.parametrize("frontend_step", ["ci", "run"])
@pytest.mark.parametrize("frontend_stderr", [False, True])
@pytest.mark.parametrize("output", [
    "npm ERR! code ELIFECYCLE\n",
    "npm error command failed\n",
    "vite build failed\n",
    "Error: build failed\n    at build (build.js:1:1)\n",
    "Error in /project/node_modules/tool/index.mjs\n",
])
def test_real_command_abort_with_js_output_remains_fatal(
    tmp_path, frontend_step, frontend_stderr, output,
):
    result, calls = run_verify(
        tmp_path, docker_status=0, frontend_step=frontend_step, frontend_statuses=(134,),
        frontend_output=output, frontend_stderr=frontend_stderr,
    )
    assert result.returncode == 134
    assert calls.count("node --frontend-command") == 2
    assert output in result.stdout + result.stderr
    assert "FAIL: frontend (exit 134)" in result.stderr
    assert "VERIFY: FAILED (exit 134)" in result.stdout
    assert "SKIP:" not in result.stdout
    assert result.stdout.count("yeniden deneme 1/1") == 1
    assert (tmp_path / "verify-node-crash.txt").is_file()


@pytest.mark.parametrize("status", [127, 1, 2])
@pytest.mark.parametrize("frontend_step", ["ci", "run"])
def test_other_real_command_errors_remain_fatal(tmp_path, status, frontend_step):
    result, calls = run_verify(
        tmp_path, docker_status=0, frontend_step=frontend_step, frontend_statuses=(status,),
    )
    assert result.returncode == status
    assert calls.count("node --frontend-command") == 1
    assert f"VERIFY: FAILED (exit {status})" in result.stdout
    assert "SKIP:" not in result.stdout
    assert "yeniden deneme" not in result.stdout
    assert not (tmp_path / "verify-node-crash.txt").exists()


@pytest.mark.parametrize("frontend_step", ["ci", "run"])
@pytest.mark.parametrize("output", ["", "Abort trap: 6\n 1: 0x100 node::Abort()\n"])
def test_required_node_real_command_abort_fails_explicitly(tmp_path, frontend_step, output):
    result, calls = run_verify(
        tmp_path, docker_status=0, frontend_step=frontend_step,
        frontend_statuses=(134,), require_node=True, frontend_output=output,
    )
    assert result.returncode == 134
    assert calls.count("node --frontend-command") == 2
    assert "IZGE_VERIFY_REQUIRE_NODE=1 (gerçek komut 134)" in result.stderr
    assert "SKIP:" not in result.stdout
    assert "VERIFY: FAILED (exit 134)" in result.stdout
    assert (tmp_path / "verify-node-crash.txt").is_file()


@pytest.mark.parametrize("statuses", [(0,), (134, 0)])
@pytest.mark.parametrize("frontend_step", ["ci", "run"])
def test_real_command_success_builds_normally(tmp_path, statuses, frontend_step):
    result, calls = run_verify(
        tmp_path, docker_status=0, frontend_step=frontend_step, frontend_statuses=statuses,
    )
    assert result.returncode == 0, result.stderr
    assert calls.count("node --frontend-command") == len(statuses)
    assert "npm run build --prefix frontend" in calls
    assert "npx playwright test" in calls
    assert "SKIP:" not in result.stdout
    assert "\nVERIFY: PASSED\n" in result.stdout


@pytest.mark.parametrize("status", [1, 2, 127])
def test_retry_error_does_not_turn_into_skip(tmp_path, status):
    result, calls = run_verify(tmp_path, docker_status=0, frontend_statuses=(134, status))
    assert result.returncode == status
    assert calls.count("node --frontend-command") == 2
    assert "SKIP:" not in result.stdout
    assert f"VERIFY: FAILED (exit {status})" in result.stdout


@pytest.mark.parametrize("outputs", [
    ("npm error build failed\n", "Abort trap: 6\n"),
    ("Abort trap: 6\n", "vite build failed\n"),
])
def test_js_trace_in_either_attempt_prevents_skip(tmp_path, outputs):
    result, calls = run_verify(
        tmp_path, docker_status=0, frontend_statuses=(134,),
        frontend_output=outputs[0], frontend_retry_output=outputs[1],
    )
    assert result.returncode == 134
    assert calls.count("node --frontend-command") == 2
    assert "SKIP:" not in result.stdout
    assert "VERIFY: FAILED (exit 134)" in result.stdout
    assert all(output in result.stdout for output in outputs)
