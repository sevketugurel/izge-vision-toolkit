# Verification-only thread budget; never export these into the worker environment.
VERIFY_NODE_OPTIONS=--v8-pool-size=2
VERIFY_UV_THREADPOOL_SIZE=2
VERIFY_NODE_CRASH_PATH="$PWD/verify-node-crash.txt"
VERIFY_NODE_CRASH_RECORDED=0

verify_node_command() {
  NODE_OPTIONS="$VERIFY_NODE_OPTIONS" UV_THREADPOOL_SIZE="$VERIFY_UV_THREADPOOL_SIZE" "$@"
}

# Best effort only. Keep the first 64 KiB, discarding the tail; never dump env or argv.
verify_node_crash_dump() {
  {
    printf '%s\n' 'node -v'
    verify_node_command node -v || true
    printf '%s\n' 'uname -a'
    uname -a || true
    printf '%s\n' 'ulimit -a'
    ulimit -a || true
    printf '%s\n' 'sysctl -n vm.swapusage'
    sysctl -n vm.swapusage || true
    printf '%s\n' 'vm_stat (first 12 lines)'
    vm_stat | head -12 || true
    printf '%s\n' 'ps -Ao pid,ppid,nlwp,rss,comm (top 15 by threads, then RSS)'
    ps -Ao pid,ppid,nlwp,rss,comm | sort -k3,3nr -k4,4nr | head -15 || true
    printf '%s\n' 'launchctl (jetsam|cpu time|thread only)'
    launchctl print "gui/$(id -u)/com.izge.harness" | grep -Ei 'jetsam|cpu time|thread' || true
    printf '%s\n' 'node-*.ips written in the last 3 minutes (count)'
    find "$HOME/Library/Logs/DiagnosticReports" -type f -name 'node-*.ips' -mmin -3 | wc -l || true
  } 2>/dev/null | head -c 65536 > "$VERIFY_NODE_CRASH_PATH" 2>/dev/null || true
  VERIFY_NODE_CRASH_RECORDED=1
}

verify_node_diagnostic_suffix() {
  if [ "$VERIFY_NODE_CRASH_RECORDED" = 1 ]; then
    printf ' — tanı: %s' "$VERIFY_NODE_CRASH_PATH"
  fi
}

# Preserve build/install failures, including 134, after collecting diagnostics.
verify_frontend_command() {
  verify_frontend_status=0
  verify_node_command "$@" || verify_frontend_status=$?
  if [ "$verify_frontend_status" = 134 ]; then
    verify_node_crash_dump
    printf 'FAIL: frontend (exit 134)%s\n' "$(verify_node_diagnostic_suffix)" >&2
  fi
  return "$verify_frontend_status"
}

# Sourced by verify.sh; probe once, preserving Docker's environment (including DOCKER_HOST).
verify_docker_init() {
  VERIFY_DOCKER_AVAILABLE=0
  VERIFY_SKIPPED=
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    VERIFY_DOCKER_AVAILABLE=1
  elif [ "${IZGE_VERIFY_REQUIRE_DOCKER:-0}" = 1 ]; then
    printf '%s\n' 'ERROR: Docker erişilemiyor; IZGE_VERIFY_REQUIRE_DOCKER=1' >&2
    return 1
  fi
}

# Use as an if condition; skipped steps are recorded for the partial summary.
verify_docker_step() {
  if [ "$VERIFY_DOCKER_AVAILABLE" = 1 ]; then
    return 0
  fi
  printf 'SKIP: %s (docker yok — sandbox)\n' "$1"
  VERIFY_SKIPPED="${VERIFY_SKIPPED:+$VERIFY_SKIPPED, }$1"
  return 1
}

# Retry the version probe only; the runtime probe remains a separate check.
verify_node_init() {
  VERIFY_NODE_AVAILABLE=0
  VERIFY_NODE_SKIP_REASON="node sandbox'ta başlatılamadı"
  verify_node_attempt=1
  while [ "$verify_node_attempt" -le 3 ]; do
    verify_node_status=0
    verify_node_version=$(verify_node_command node --version 2>/dev/null) || verify_node_status=$?
    if [ "$verify_node_status" = 134 ]; then
      verify_node_crash_dump
    fi
    printf 'VERIFY: node --version deneme %s/3 (exit %s)\n' \
      "$verify_node_attempt" "$verify_node_status"
    if [ "$verify_node_status" = 0 ] && [ -n "$verify_node_version" ]; then
      verify_node_major=${verify_node_version#v}
      verify_node_major=${verify_node_major%%.*}
      case "$verify_node_major" in
        ''|*[!0-9]*) break ;;
      esac
      if [ "$verify_node_major" -lt 20 ]; then
        printf "ERROR: node v%s < 20; npm PATH'i nvm node 22'yi göstermeli\n" \
          "$verify_node_major" >&2
        return 1
      fi
      # Initialize V8/platform too (existing runtime probe, task #461).
      verify_node_runtime_status=0
      verify_node_command node -p 1 >/dev/null 2>&1 || verify_node_runtime_status=$?
      if [ "$verify_node_runtime_status" = 0 ]; then
        VERIFY_NODE_AVAILABLE=1
      elif [ "$verify_node_runtime_status" = 134 ]; then
        verify_node_crash_dump
      fi
      break
    fi
    # Missing node (127) and other errors are not transient startup failures.
    if [ "$verify_node_status" != 134 ] && \
      { [ "$verify_node_status" != 0 ] || [ -n "$verify_node_version" ]; }; then
      break
    fi
    if [ "$verify_node_attempt" = 3 ]; then
      VERIFY_NODE_SKIP_REASON="node sandbox'ta başlatılamadı — 3 deneme"
      break
    fi
    sleep 2
    verify_node_attempt=$((verify_node_attempt + 1))
  done
  if [ "$VERIFY_NODE_AVAILABLE" = 0 ] && [ "${IZGE_VERIFY_REQUIRE_NODE:-0}" = 1 ]; then
    printf 'ERROR: node başlatılamadı; IZGE_VERIFY_REQUIRE_NODE=1%s\n' \
      "$(verify_node_diagnostic_suffix)" >&2
    return 1
  fi
}

# Use as an if condition; same partial-summary bookkeeping as verify_docker_step.
verify_node_step() {
  if [ "$VERIFY_NODE_AVAILABLE" = 1 ]; then
    return 0
  fi
  printf 'SKIP: %s (%s)%s\n' "$1" "$VERIFY_NODE_SKIP_REASON" \
    "$(verify_node_diagnostic_suffix)"
  VERIFY_SKIPPED="${VERIFY_SKIPPED:+$VERIFY_SKIPPED, }$1"
  return 1
}

# Probe after installing Chromium, so a clean checkout can still run e2e.
verify_chromium_init() {
  VERIFY_CHROMIUM_AVAILABLE=0
  if command -v node >/dev/null 2>&1 && (cd frontend && verify_node_command node -e '
    const { chromium } = require("playwright");
    (async () => {
      const browser = await chromium.launch({ headless: true, timeout: 5000 });
      await browser.close();
    })().catch(() => process.exit(1));
  ') >/dev/null 2>&1; then
    VERIFY_CHROMIUM_AVAILABLE=1
  elif [ "${IZGE_VERIFY_REQUIRE_CHROMIUM:-0}" = 1 ]; then
    printf '%s\n' 'ERROR: chromium başlatılamadı; IZGE_VERIFY_REQUIRE_CHROMIUM=1' >&2
    return 1
  fi
}

verify_chromium_step() {
  if [ "$VERIFY_CHROMIUM_AVAILABLE" = 1 ]; then
    return 0
  fi
  printf "SKIP: %s (chromium sandbox'ta başlatılamadı)\n" "$1"
  VERIFY_SKIPPED="${VERIFY_SKIPPED:+$VERIFY_SKIPPED, }$1"
  return 1
}
