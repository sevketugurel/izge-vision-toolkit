# Changelog

All notable changes will be recorded here. The project follows [Keep a Changelog](https://keepachangelog.com/) and, once released, pre-1.0 [SemVer](https://semver.org/).

## [0.1.0] — Initial source release

### Added
- English README, CONTRIBUTING, SECURITY, SUPPORT, CODE_OF_CONDUCT.
- GitHub issue and pull request templates.
- GitHub Actions CI (backend lint and unit tests, PostgreSQL 18 integration, frontend build) with read-only permissions and SHA-pinned actions.
- Dependabot configuration for Actions, uv and npm.
- English architecture and roadmap summaries (`docs/architecture.en.md`, `docs/roadmap.en.md`).

- Optional bearer-token guard for `/api/*` (`IZGE_API_TOKEN`), off by default.
- Security workflow: gitleaks (full history), pip-audit, npm audit; Docker build job in CI.
- Hand-computed regression test for `scripts/score_tracks.py` (synthetic scene).

### Changed
- Previous Turkish README moved to `README.tr.md`; design-language tests now read it there.
- Compose app containers drop all capabilities and set `no-new-privileges`.
- Dependencies: pillow 12.3.0 to 13.0.0 (42 advisories), pytest 9.0.3, pyasn1 0.6.5, plus refreshed transitives; `scipy` is now declared explicitly.
- Docker image applies Debian security updates at build time.

### Fixed
- A malformed API answer on the control-center or admin pages crashed the whole UI; pages now show an inline error (error boundary + response shape checks, with an e2e regression test).
- `docker compose up --build` failed because the frontend stage could not find `design/motion-tokens.json`.
- Test results no longer depend on an ambient `IZGE_CAMERA_CONFIG_DIRS`.
