# Security Policy

## Supported versions

İzge has no tagged release. Only the latest commit on the default branch receives security fixes.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting for this repository (Security tab, "Report a vulnerability"). Include the affected commit, reproduction steps and impact. Do not include real credentials or footage.

You can expect an acknowledgement within about 7 days. This is a single-maintainer project; there is no bug-bounty program.

## Threat model and known limitations

İzge is designed for **local, single-operator research use**. It is not hardened for exposure to the internet.

- **Authentication is minimal.** By default the HTTP API has no authentication. Setting `IZGE_API_TOKEN` enables an opt-in shared bearer token on every `/api/*` route except `/api/health`. It is one shared secret: no user accounts, no per-user authorization, no rate limiting, no TLS. The bundled web UI does **not** send the token, so enable it for scripted/API use or behind a reverse proxy that injects the header. Do not expose the API to untrusted networks, with or without the token.
- **Compose is local-only by default.** The published port binds to `127.0.0.1`, the database port is not published, and app containers run as a non-root user with all Linux capabilities dropped and `no-new-privileges`.
- **Optional powerful features are off by default.** The training control center (`IZGE_CONTROL_CENTER_ENABLED`) and admin planning (`IZGE_ADMIN_PLANNING_ENABLED`) require explicit opt-in. The native training runner has its own token and binds to `127.0.0.1` unless told otherwise.
- **Uploads are untrusted input** processed with FFmpeg/PyAV. Upload size is limited (`MAX_UPLOAD_BYTES`), but media parsing has not been security-audited or fuzzed.
- **Camera credentials** (for example `IZGE_RTSP_CAMERA_*`) must come from environment variables and are not meant to be returned by the API.
- **Hugging Face model downloads** are not pinned to a revision in code (bandit B615); pin revisions if you use gated models in a sensitive environment.

## What is checked automatically

CI runs gitleaks over the full history, `pip-audit` on the locked Python dependencies, `npm audit` on production frontend dependencies, and builds the Docker image. The last manual review is in `docs/oss-readiness/phase-2/SECURITY_REVIEW.md`. These checks reduce risk; they do not prove the absence of vulnerabilities.

## Data handling

Never commit farm footage, camera credentials, or personal data. If you find any in the repository or its history, report it privately.
