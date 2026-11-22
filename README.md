# İzge

İzge is a research-stage toolkit for turning barn camera footage into reviewable, auditable observations about cattle. It is a modular monolith (FastAPI, PostgreSQL, a job-queue worker, a React review UI) with a computer-vision layer for video decoding, tracking evaluation and annotation auditing.

**Status: early development, single maintainer, no release yet.** There is no trained behaviour model in this repository, and no published accuracy figure for real cattle. The project's central rule is that unknown stays unknown: results carry an origin (`manual`, `model`, `demo`, `unavailable`) and demo output is never presented as real.

The original Turkish README is kept as [README.tr.md](README.tr.md).

## What exists today

| Capability | Status | Evidence |
|---|---|---|
| Video ingest, PTS-accurate frame timing, gap and non-monotonic-time diagnostics (PyAV) | Implemented, unit-tested | `backend/izge/video.py`, `backend/izge/vision/timing.py` |
| Job queue with leases, worker, atomic result publishing (PostgreSQL 18) | Implemented; integration tests not run in this audit (no Docker) | `backend/izge/worker.py`, `backend/tests/test_integration.py` |
| Human review UI: labels, evidence clips, JSON/CSV export | Implemented; frontend builds, e2e not run | `frontend/` |
| Multi-object tracker + MOT metrics (MOTA, IDF1, HOTA) and scoring CLI | Implemented, unit-tested; evaluated on synthetic data only | `backend/izge/vision/tracking.py`, `track_eval.py` |
| Annotation audit, frame-quality scan, pair-interaction candidate filter | Implemented, unit-tested | `backend/izge/vision/` |
| Per-stall "feeding time" pipeline (frozen DINOv3 backbone + classifier, human-label gate) | Partially implemented: code and training CLI exist; **no trained model is shipped and none has passed its acceptance gate** | `backend/izge/feeding/`, `docs/V1_PLAN.md` |
| Animal detection and segmentation experiments (RF-DETR-Seg) | Experiments only, not wired into the worker | `experiments/`, `docs/DECISIONS.md` |
| Posture classification | Research code behind an explicit human-label training gate; no trained model is shipped | `docs/POSTURE_TRAINING_GATE.md` |
| Individual animal identification, behaviour classification, estrus/health detection | **Not implemented** | |
| Authentication / authorization | **Not implemented.** Do not expose the API beyond localhost | `docs/ROADMAP.md` |

Work on a live-inference admin panel and live-camera tooling exists in unmerged branches and pull requests; it is not part of `main`.

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

Tracker throughput on random synthetic boxes (CPU, tracker only, no detection): `uv run python scripts/bench_tracker.py --help`.

## Requirements

- Python 3.14 (pinned: `>=3.14,<3.15`) and [uv](https://docs.astral.sh/uv/)
- Node 22.18 or newer for the frontend (Node 18 fails to build it)
- FFmpeg/ffprobe for video tests
- Docker, for the full stack and the PostgreSQL 18 integration tests
- Optional `ml` extra (PyTorch, transformers) and a Hugging Face token for the gated DINOv3 weights

## Run the tests

```sh
uv sync --frozen
uv run ruff check backend scripts
uv run pytest -q --ignore=backend/tests/test_integration.py --ignore=backend/tests/test_feeding_integration.py
```

Last verified run for this README: ruff passed; 440 passed, 3 skipped in 126 s on macOS arm64 (no database, no GPU).

Frontend:

```sh
cd frontend && npm ci && npm run build   # requires Node >= 22.18
```

PostgreSQL 18 integration tests use a disposable container and never touch a real database:

```sh
docker compose -f compose.test.yaml up -d --wait
export DATABASE_URL=postgresql+psycopg://izge:izge-test@127.0.0.1:55432/izge_test PYTHONPATH=backend
uv run alembic upgrade head
IZGE_TEST_DATABASE=1 MEDIA_ROOT="$(mktemp -d)" uv run pytest -q backend/tests/test_integration.py
```

These integration commands follow `scripts/verify.sh` but **have not been run in this audit** because no Docker daemon was available.

## Run the application

```sh
cp .env.example .env
docker compose up --build
```

UI at http://127.0.0.1:8000, OpenAPI at http://127.0.0.1:8000/docs. The port binds to localhost only. This path was **not executed in this audit** (no Docker daemon); see [docs/oss-readiness/quality-validation.md](docs/oss-readiness/quality-validation.md).

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

- No authentication or authorization; single-tenant assumptions in several places.
- No trained model passes its acceptance gate. Any metric quoted in the repository is either synthetic (`origin=demo`) or explicitly model-derived and unvalidated.
- Most project documentation is in Turkish.
- `main` is behind `develop` and several open pull requests.
- Python 3.14 and recent dependency versions are pinned; older interpreters are unsupported.
- The repository still contains an unrelated agent-orchestration subproject (`harness/`) and internal planning documents; see the OSS readiness audit.
- Some code and documentation were written with AI assistance (see Maintainers).

## Roadmap

[docs/ROADMAP.md](docs/ROADMAP.md) (Turkish) and the short English summary in [docs/oss-readiness/opensource-strategy.md](docs/oss-readiness/opensource-strategy.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md). Questions: [SUPPORT.md](SUPPORT.md).

## Maintainers

Created and maintained by Şevket Uğurel ([@sevketugurel](https://github.com/sevketugurel)), who authored about 99% of the commits. A large share of commits are co-authored with AI coding assistants (Claude, per commit trailers); all were reviewed and merged by the maintainer.

## License

**No license has been chosen yet**, so by default all rights are reserved and the code is not open source. See [docs/oss-readiness/opensource-strategy.md](docs/oss-readiness/opensource-strategy.md). Third-party licence notes: [docs/THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md). Models such as DINOv3 carry their own licence terms.
