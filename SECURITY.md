# Security Policy

## Supported versions

İzge has no tagged release. Only the latest commit on `main` receives security fixes.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting for this repository (Security tab, "Report a vulnerability"). Include affected commit, reproduction steps, and impact. Do not include real credentials or footage.

You can expect an acknowledgement within about 7 days. This is a single-maintainer project; there is no bug-bounty program.

## Known security limitations

- The HTTP API has **no authentication or authorization**. Run it only bound to localhost (the default Compose file does this) or behind your own authenticated reverse proxy. Do not expose it to the internet.
- Camera RTSP URLs may contain credentials; they must come from environment variables and are not meant to be returned by the API.
- Uploaded video is processed with FFmpeg/PyAV. Treat uploads as untrusted input.

## Data handling

Never commit farm footage, camera credentials, or personal data. If you find any in the repository or its history, report it privately.
