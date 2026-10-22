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
