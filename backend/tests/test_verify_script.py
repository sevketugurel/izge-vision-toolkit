"""Exercise the real verification flow with isolated executables; never use Docker."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SKIPPED = ("postgres", "postgres-integration", "compose", "playwright-e2e")


def run_verify(tmp_path, docker_status=None, require=False, docker_host="", fail_command=""):
    for directory in ("scripts", "harness", "frontend", "bin"):
        (tmp_path / directory).mkdir()
    for name in ("verify.sh", "verify_docker.sh"):
        shutil.copyfile(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    # An isolated PATH guarantees that the missing-docker case cannot find a host daemon.
    for name in ("dirname", "mktemp", "rm"):
        (tmp_path / "bin" / name).symlink_to(shutil.which(name))
    log = tmp_path / "calls.log"
    for name in ("uv", "npm", "npx", "curl", "docker"):
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
        )
        executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tmp_path / "bin"),
        "CALL_LOG": str(log),
        "FAIL_COMMAND": fail_command,
        "IZGE_VERIFY_REQUIRE_DOCKER": "1" if require else "0",
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
