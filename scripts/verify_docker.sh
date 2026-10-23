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

# Initialize V8/platform too (node may abort with 134/SIGABRT inside the sandbox).
verify_node_init() {
  VERIFY_NODE_AVAILABLE=0
  if command -v node >/dev/null 2>&1 && node -p 1 >/dev/null 2>&1; then
    VERIFY_NODE_AVAILABLE=1
  elif [ "${IZGE_VERIFY_REQUIRE_NODE:-0}" = 1 ]; then
    printf '%s\n' 'ERROR: node başlatılamadı; IZGE_VERIFY_REQUIRE_NODE=1' >&2
    return 1
  fi
}

# Use as an if condition; same partial-summary bookkeeping as verify_docker_step.
verify_node_step() {
  if [ "$VERIFY_NODE_AVAILABLE" = 1 ]; then
    return 0
  fi
  printf 'SKIP: %s (node sandbox'"'"'ta başlatılamadı)\n' "$1"
  VERIFY_SKIPPED="${VERIFY_SKIPPED:+$VERIFY_SKIPPED, }$1"
  return 1
}

# Probe after installing Chromium, so a clean checkout can still run e2e.
verify_chromium_init() {
  VERIFY_CHROMIUM_AVAILABLE=0
  if command -v node >/dev/null 2>&1 && (cd frontend && node -e '
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
