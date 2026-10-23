"""Run verification with fake Node failures and bounded, isolated diagnostics."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def verification(tmp_path):
    for name in ("scripts", "frontend", "harness", "bin"):
        (tmp_path / name).mkdir()
    for name in ("verify.sh", "verify_docker.sh"):
        shutil.copyfile(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    for name in ("dirname", "mktemp", "rm", "head", "sort", "grep", "find", "wc", "id", "cat"):
        (tmp_path / "bin" / name).symlink_to(shutil.which(name))
    fake = tmp_path / "fake-tool"
    fake.write_text(f"#!{sys.executable}\n" + '''
import json
import os
from pathlib import Path
import sys
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(json.dumps([name, args, os.getenv("NODE_OPTIONS"),
                          os.getenv("UV_THREADPOOL_SIZE")]) + "\\n")
if name == "node":
    if args == ["--version"]:
        print("v22.18.0")
        sys.exit(int(os.getenv("VERSION_STATUS", "0")))
    if args == ["-p", "1"]:
        sys.exit(int(os.getenv("RUNTIME_STATUS", "0")))
    if args == ["-v"]:
        print("v22.18.0" + "x" * int(os.getenv("DIAGNOSTIC_BYTES", "0")))
        sys.exit(134)  # Even diagnostics failing must not stop collection.
if name == "npm" and args[0] == os.getenv("FAIL_STEP"):
    stream = os.getenv("FAIL_OUTPUT_STREAM")
    if stream:
        print(os.getenv("FAIL_OUTPUT", "TypeError: frontend failed\\n    at build (build.js:1:1)"),
              file=sys.stderr if stream == "stderr" else sys.stdout)
    sys.exit(134)
if name == "launchctl":
    print("jetsam = 1\\ncpu time = 2\\nthread count = 3\\nSECRET_ENV=do-not-copy")
elif name == "ps":
    print("PID PPID NLWP RSS COMM")
    for i in range(1, 21):
        print(f"{i} 1 {i} {i * 10} process-{i}")
elif name == "vm_stat":
    for i in range(20):
        print(f"vm-line-{i}")
elif name == "sysctl":
    sys.exit(1)
elif name == "uname":
    print("test-system")
''')
    fake.chmod(0o755)
    for name in ("node", "npm", "npx", "uv", "docker", "curl", "sleep", "launchctl",
                 "ps", "vm_stat", "sysctl", "uname"):
        (tmp_path / "bin" / name).symlink_to(fake)
    reports = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports.mkdir(parents=True)
    for name in ("node-recent.ips", "node-old.ips", "other.ips"):
        (reports / name).touch()
    old = time.time() - 240
    os.utime(reports / "node-old.ips", (old, old))

    def run(**overrides):
        env = {
            "PATH": str(tmp_path / "bin"), "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path), "CALL_LOG": str(tmp_path / "calls.jsonl"),
            "SECRET_ENV": "do-not-copy", **overrides,
        }
        result = subprocess.run(
            ["/bin/sh", str(tmp_path / "scripts" / "verify.sh")],
            env=env, capture_output=True, text=True, timeout=20,
        )
        calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
        return result, calls, tmp_path / "verify-node-crash.txt"

    return run


@pytest.mark.parametrize("probe", ["VERSION_STATUS", "RUNTIME_STATUS"])
def test_probe_abort_skips_with_diagnostics(verification, probe):
    result, calls, diagnostic = verification(**{probe: "134"})
    assert result.returncode == 0, result.stderr
    skip = next(line for line in result.stdout.splitlines() if line.startswith("SKIP: frontend"))
    assert str(diagnostic) in skip
    assert "KISMİ" in result.stdout
    assert not any(name in ("npm", "npx") for name, *_ in calls)
    assert sum(name == "node" and args == ["--version"] for name, args, *_ in calls) == (
        3 if probe == "VERSION_STATUS" else 1
    )
    text = diagnostic.read_text()
    assert 0 < diagnostic.stat().st_size <= 65536
    for expected in ("node -v", "test-system", "ulimit -a", "sysctl -n vm.swapusage",
                     "vm-line-11", "20 1 20 200 process-20", "jetsam = 1", "cpu time = 2",
                     "thread count = 3", "last 3 minutes (count)\n"):
        assert expected in text
    assert "vm-line-12" not in text
    assert "5 1 5 50 process-5" not in text
    assert "do-not-copy" not in text
    assert text.rstrip().endswith("1")
    launch = next(args for name, args, *_ in calls if name == "launchctl")
    assert launch[0] == "print"
    assert launch[1].endswith("/com.izge.harness")


def test_diagnostic_tail_is_truncated_at_64_kib(verification):
    result, _, diagnostic = verification(VERSION_STATUS="134", DIAGNOSTIC_BYTES="90000")
    assert result.returncode == 0, result.stderr
    assert diagnostic.stat().st_size == 65536
    assert diagnostic.read_text().startswith("node -v\nv22.18.0")
    assert "uname -a" not in diagnostic.read_text()


@pytest.mark.parametrize("step", ["ci", "run"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_frontend_abort_preserves_failure_and_reports_path(verification, step, stream):
    result, calls, diagnostic = verification(FAIL_STEP=step, FAIL_OUTPUT_STREAM=stream)
    assert result.returncode == 134
    assert diagnostic.is_file()
    assert "TypeError: frontend failed" in result.stdout
    assert sum(name == "npm" and args[0] == step for name, args, *_ in calls) == 2
    assert "SKIP: frontend" not in result.stdout
    assert f"FAIL: frontend (exit 134) — tanı: {diagnostic}" in result.stderr
    assert "VERIFY: FAILED (exit 134)" in result.stdout


@pytest.mark.parametrize("step", ["ci", "run"])
def test_silent_frontend_abort_retries_and_skips_with_diagnostics(verification, step):
    result, calls, diagnostic = verification(FAIL_STEP=step)
    assert result.returncode == 0, result.stderr
    assert diagnostic.is_file()
    assert 0 < diagnostic.stat().st_size <= 65536
    assert sum(name == "npm" and args[0] == step for name, args, *_ in calls) == 2
    assert result.stdout.count("yeniden deneme 1/1") == 1
    assert (
        "SKIP: frontend (node sandbox'ta başlatılamadı — gerçek komut 134)"
        f" — tanı: {diagnostic}"
    ) in result.stdout
    assert "VERIFY: PASSED (KISMİ — atlanan: frontend, playwright-e2e)" in result.stdout
    assert "FAIL: frontend" not in result.stderr
    assert not any(name == "npx" for name, *_ in calls)


def test_required_node_abort_reports_diagnostic_path(verification):
    result, _, diagnostic = verification(VERSION_STATUS="134", IZGE_VERIFY_REQUIRE_NODE="1")
    assert result.returncode == 1
    assert str(diagnostic) in result.stderr
    assert diagnostic.is_file()


@pytest.mark.parametrize("inherited", [False, True])
def test_thread_budget_is_scoped_and_healthy_probe_builds(verification, inherited):
    original = {"NODE_OPTIONS": "worker-option", "UV_THREADPOOL_SIZE": "7"} if inherited else {}
    result, calls, diagnostic = verification(**original)
    assert result.returncode == 0, result.stderr
    assert not diagnostic.exists()
    assert any(name == "npm" and args[0:2] == ["run", "build"] for name, args, *_ in calls)
    assert any(name == "npx" for name, *_ in calls)
    for name, _, node_options, pool_size in calls:
        if name in ("node", "npm", "npx"):
            assert (node_options, pool_size) == ("--v8-pool-size=2", "2")
        else:
            assert node_options == original.get("NODE_OPTIONS")
            assert pool_size == original.get("UV_THREADPOOL_SIZE")


@pytest.fixture(params=[
    ("task-691-a550", "0x1004c1720", "60132"),
    ("task-697-a554", "0x10491d720", "76524"),
    ("task-700-a557", "0x1047d5720", "2130"),
    ("task-683-a544", "0x100575720", "17702"),
], ids=lambda case: case[0])
def native_abort_log(request):
    # Representative native frames and shell abort line from the four real logs.
    _, address, pid = request.param
    return (
        "----- Native stack trace -----\n\n"
        f" 3: {address} node::Start(int, char**) "
        "[~/.nvm/versions/node/v22.18.0/bin/node]\n"
        " 4: 0x19bc6be80 start [/usr/lib/dyld]\n"
        f"./scripts/verify_docker.sh: line 7: {pid} Abort trap: 6           "
        'NODE_OPTIONS="$VERIFY_NODE_OPTIONS" UV_THREADPOOL_SIZE="$VERIFY_UV_THREADPOOL_SIZE" "$@"\n'
    )


@pytest.mark.parametrize("step", ["ci", "run"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_native_frontend_abort_retries_and_skips_with_diagnostics(
    verification, native_abort_log, step, stream,
):
    result, calls, diagnostic = verification(
        FAIL_STEP=step, FAIL_OUTPUT_STREAM=stream, FAIL_OUTPUT=native_abort_log,
    )
    assert result.returncode == 0, result.stderr
    assert native_abort_log in result.stdout
    assert sum(name == "npm" and args[0] == step for name, args, *_ in calls) == 2
    assert result.stdout.count("yeniden deneme 1/1") == 1
    assert "SKIP: frontend" in result.stdout
    assert "VERIFY: PASSED (KISMİ — atlanan: frontend, playwright-e2e)" in result.stdout
    assert "\nVERIFY: PASSED\n" not in result.stdout
    assert str(diagnostic) in result.stdout
    assert 0 < diagnostic.stat().st_size <= 65536
    assert not any(name == "npx" for name, *_ in calls)
    if step == "ci":
        assert not any(name == "npm" and args[:2] == ["run", "build"] for name, args, *_ in calls)
