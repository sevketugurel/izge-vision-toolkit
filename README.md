# İzge

İzge is a research-stage toolkit for turning barn camera footage into reviewable, auditable observations about cattle. It is a modular monolith (FastAPI, PostgreSQL, a job-queue worker, a React review UI) with a computer-vision layer for video decoding, tracking evaluation and annotation auditing.

**Status: early development, single maintainer, no release yet.** There is no trained behaviour model in this repository, and no published accuracy figure for real cattle. The project's central rule is that unknown stays unknown: results carry an origin (`manual`, `model`, `demo`, `unavailable`) and demo output is never presented as real.

The original Turkish README is kept as [README.tr.md](README.tr.md).

## What exists today

| Capability | Status | Evidence |
|---|---|---|
| Video ingest, PTS-accurate frame timing, gap and non-monotonic-time diagnostics (PyAV) | Implemented, unit-tested | `backend/izge/video.py`, `backend/izge/vision/timing.py` |
| Job queue with leases, worker, atomic result publishing (PostgreSQL 18) | Implemented and verified: migration round trip and 14 integration tests pass on PostgreSQL 18.0 | `backend/izge/worker.py`, `backend/tests/test_integration.py` |
| Human review UI: labels, evidence clips, JSON/CSV export | Implemented and verified: builds; 8 of 9 Playwright e2e tests pass against the Compose stack, 1 fails (see Known limitations) | `frontend/` |
| Multi-object tracker + MOT metrics (MOTA, IDF1, HOTA) and scoring CLI | Implemented, unit-tested; evaluated on synthetic data only | `backend/izge/vision/tracking.py`, `track_eval.py` |
| Annotation audit, frame-quality scan, pair-interaction candidate filter | Implemented, unit-tested | `backend/izge/vision/` |
| Per-stall "feeding time" pipeline (frozen DINOv3 backbone + classifier, human-label gate) | Partially implemented: code and training CLI exist; **no trained model is shipped and none has passed its acceptance gate** | `backend/izge/feeding/`, `docs/V1_PLAN.md` |
| Animal detection and segmentation experiments (RF-DETR-Seg) | Experiments only, not wired into the worker | `experiments/`, `docs/DECISIONS.md` |
| Posture classification | Research code behind an explicit human-label training gate; no trained model is shipped | `docs/POSTURE_TRAINING_GATE.md` |
| Individual animal identification, behaviour classification, estrus/health detection | **Not implemented** | |
| Optional shared bearer token for `/api/*` (`IZGE_API_TOKEN`) | Implemented and verified (unit tests and a live container). One shared secret; the bundled UI does not send it | `backend/izge/auth.py`, `SECURITY.md` |
| User accounts, per-user authorization, TLS | **Not implemented.** Do not expose the API beyond localhost | `docs/ROADMAP.md` |
| Dataset inventory/validation, authenticated native training runner (localhost), training control center | Implemented in `develop`; off by default, unit-tested where dependencies allow; GPU training itself not verified here | `backend/izge/datasets/`, `backend/izge/training/`, `docs/CONTROL_CENTER.md` |

Live-inference admin pages and live-camera tooling exist in open pull requests and are not part of this baseline.

## Reproducible example (no database, no GPU, no real data)

A deterministic synthetic scene is tracked and scored. This tests the tracking and metric code, **not** cattle detection; the objects are synthetic blobs.

```sh
uv sync --frozen
uv run python scripts/vision_rd_demo.py --output-dir /tmp/izge_demo
```

Expected output (identical on every run with the default seed; verified on macOS arm64, Python 3.14.4):

```json
{
  "origin": "demo",
  "metrics": { "frames": 80, "gt_boxes": 108, "true_positives": 87, "false_negatives": 21,
               "false_positives": 18, "id_switches": 1, "fragmentations": 4,
               "mota": 0.6296296296296297, "recall": 0.8055555555555556,
               "precision": 0.8285714285714286, "gt_tracks": 3,
               "mostly_tracked_tracks": 2, "iou_threshold": 0.5 } }
```

Tracker throughput on random synthetic boxes (CPU, tracker only, no detection; 0.61 to 0.63 ms per update on an Apple M1 Pro): `uv run python scripts/bench_tracker.py`.

### Metric correctness check you can verify by hand

`backend/tests/test_score_tracks_cli.py` builds a 10-frame, single-track scene whose prediction changes ID after frame 5 and scores it with `scripts/score_tracks.py`. The expected values follow from the definitions: MOTA = 1 - 1/10 = 0.9, IDF1 = 2·5/(2·5+5+5) = 0.5, HOTA = sqrt(1.0 · 0.5) = 0.7071. The test asserts all three. Synthetic data, so it validates the metric code, not any tracker.

## Requirements

- Python 3.14 (pinned: `>=3.14,<3.15`) and [uv](https://docs.astral.sh/uv/)
- Node 22.18 or newer for the frontend (Node 18 fails to build it)
- FFmpeg/ffprobe for video tests
- Docker with Compose, for the full stack and the PostgreSQL 18 integration tests (verified with Docker 27.5)
- Optional `ml` extra (PyTorch, transformers) and a Hugging Face token for the gated DINOv3 weights

## Run the tests

```sh
uv sync --frozen
uv run ruff check backend scripts
uv run pytest -q --ignore=backend/tests/test_integration.py --ignore=backend/tests/test_feeding_integration.py
```

Last verified run for this README (macOS arm64, Python 3.14.4, no database, no GPU): `ruff` passed; 485 passed, 8 skipped in 93 s. With the `ml` extra installed (`uv sync --frozen --extra ml`): 491 passed, 3 skipped. The remaining skips need `mlflow` and `pytorch_lightning` from the separate training environment. Unit-test line coverage of `backend/izge` is 66.9%; the reusable vision modules are at 97% to 100% (see `docs/oss-readiness/phase-2/TECHNICAL_VALIDATION.md`).

Frontend:

```sh
cd frontend && npm ci && npm run build   # requires Node >= 22.18
```

PostgreSQL 18 integration tests use a disposable container and never touch a real database:

```sh
docker compose -f compose.test.yaml up -d --wait
export DATABASE_URL=postgresql+psycopg://izge:izge-test@127.0.0.1:55432/izge_test PYTHONPATH=backend
uv run alembic upgrade head
IZGE_TEST_DATABASE=1 MEDIA_ROOT="$(mktemp -d)" uv run pytest -q backend/tests/test_integration.py backend/tests/test_feeding_integration.py
docker compose -f compose.test.yaml down -v
```

Verified on 2026-11-22: migration upgrade, downgrade to an empty schema, upgrade, and `alembic check` (no drift) succeed on PostgreSQL 18.0, and the 14 integration tests pass. Run `unset` on any `IZGE_*` variables first; ambient `IZGE_CAMERA_CONFIG_DIRS` used to change test results (now fixed in `conftest.py`).

## Run the application

```sh
cp .env.example .env
docker compose up --build
```

UI at http://127.0.0.1:8000, OpenAPI at http://127.0.0.1:8000/docs. The port binds to localhost only. Verified on 2026-11-22: the image builds, `db`, `api` and `worker` become healthy, `migrate` exits 0, `/api/health` returns ok. This only works with the `design/` build-context fix in this branch; before it, `docker compose up --build` failed at the frontend build. The first build downloads CPU PyTorch and takes several minutes.

Browser e2e tests need the test-only camera fixtures: start the stack with `IZGE_CAMERA_CONFIG_DIRS=/app/config/cameras:/app/backend/tests/fixtures/cameras`, then `cd frontend && npx playwright install chromium && IZGE_E2E_URL=http://127.0.0.1:8000 npx playwright test`.

The synthetic demo (`docker compose exec api python -m izge.demo`) creates a clearly labelled demo farm. Without a connected model, real uploads produce `unavailable` observations by design.

## Configuration

See [.env.example](.env.example). Secrets (`POSTGRES_PASSWORD`, `HF_TOKEN`, any `IZGE_RTSP_CAMERA_*` URL) belong in `.env`, which is git-ignored. Camera crop and region configs live in `config/cameras/`; the shipped coordinates are placeholders and analysis refuses placeholder configs.

## Architecture

```mermaid
flowchart LR
  U[Browser: React review UI] --> A[FastAPI]
  A --> DB[(PostgreSQL 18)]
  A --> S[Local media storage]
  W[Worker: leases jobs] --> DB
  W --> S
  W --> V[PyAV decode + timing]
  V --> P[Analyzer: model optional]
  P --> W
```

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (Turkish) and [docs/oss-readiness/architecture-audit.md](docs/oss-readiness/architecture-audit.md).

## Known limitations

- Authentication is an opt-in single shared token; no accounts, authorization, TLS or rate limiting. Do not expose the API to untrusted networks.
- One Playwright test (`fixture-smoke.spec.ts`) fails on the "ML deneyleri" page with mocked API data (element detached while clicking); the cause was not isolated.
- The Docker image bundles PyAV's FFmpeg, which is a GPL-enabled build; do not publish the image without reading `docs/oss-readiness/phase-2/LICENSE_DECISION.md`.
- Single-tenant assumptions in several places.
- No trained model passes its acceptance gate. Any metric quoted in the repository is either synthetic (`origin=demo`) or explicitly model-derived and unvalidated.
- Most project documentation is in Turkish.
- `main` is behind `develop` and several open pull requests.
- Python 3.14 and recent dependency versions are pinned; older interpreters are unsupported. Node 22.18 or newer is required for the frontend.
- The repository still contains an unrelated agent-orchestration subproject (`harness/`) and internal planning documents; see the OSS readiness audit.
- Some code and documentation were written with AI assistance (see Maintainers).

## Roadmap

[docs/ROADMAP.md](docs/ROADMAP.md) (Turkish) and the short English summary in [docs/oss-readiness/opensource-strategy.md](docs/oss-readiness/opensource-strategy.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md). Questions: [SUPPORT.md](SUPPORT.md).

## Maintainers

Created and maintained by Sevket Ugurel ([@sevketugurel](https://github.com/sevketugurel)), who authored about 99% of the commits. About 86% of commits (239 of 277 on `develop`) carry co-author trailers for AI coding assistants (Claude Opus 5.5 and Sonnet 5.5). All pull requests were opened and merged by the maintainer; external review has not happened yet.

## License

**No license has been chosen yet**, so by default all rights are reserved and the code is not open source. See [docs/oss-readiness/opensource-strategy.md](docs/oss-readiness/opensource-strategy.md). Third-party licence notes: [docs/THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md). Models such as DINOv3 carry their own licence terms.
