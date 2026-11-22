# İzge Vision Toolkit v0.1.0 (research preview)

First public release. Research stage: no trained model ships, and no accuracy on real cattle is claimed.

## Included
- FastAPI + PostgreSQL 18 job queue with leased workers, PTS-accurate video timing (PyAV), human review UI with JSON/CSV export.
- Tracking evaluation: ByteTrack-style tracker and CLEAR-MOT / IDF1 / HOTA scorer (`scripts/score_tracks.py`) with a hand-computed regression test.
- Annotation audit, frame-quality scan, deterministic synthetic tracking demo (`scripts/vision_rd_demo.py`).
- Dataset validation and an authenticated localhost training runner (optional, off by default).
- Optional shared bearer token for `/api/*` (`IZGE_API_TOKEN`); for scripts or proxies only.

## Verified on GitHub Actions
486 unit, 14 PostgreSQL 18 integration and 18 training-runner tests; frontend and Docker image builds; gitleaks (full history), pip-audit and npm audit clean.

## Not included
Docker images (the PyAV wheel bundles a GPL-enabled FFmpeg build), model weights, datasets, farm footage.

## Known limitations
See README "Known limitations" and SECURITY.md. No user accounts or TLS; do not expose the API to untrusted networks.
