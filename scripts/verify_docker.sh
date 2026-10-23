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
    verify_node_version=$(node --version 2>/dev/null) || verify_node_status=$?
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
      if node -p 1 >/dev/null 2>&1; then
        VERIFY_NODE_AVAILABLE=1
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
    printf '%s\n' 'ERROR: node başlatılamadı; IZGE_VERIFY_REQUIRE_NODE=1' >&2
    return 1
  fi
}

# Use as an if condition; same partial-summary bookkeeping as verify_docker_step.
verify_node_step() {
  if [ "$VERIFY_NODE_AVAILABLE" = 1 ]; then
    return 0
  fi
  printf 'SKIP: %s (%s)\n' "$1" "$VERIFY_NODE_SKIP_REASON"
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
