# Contributing to İzge

Thanks for looking. İzge is an early-stage, single-maintainer project; review may be slow. Small, focused pull requests get reviewed fastest.

## Ground rules

1. **No farm data in the repository.** Do not commit footage, frames, labels from real farms, model weights, camera addresses, RTSP URLs, or credentials. Use synthetic fixtures (`backend/tests/synthetic.py`) and label them as synthetic.
2. **Never present demo or mock output as a real result.** Keep the result origins `manual`, `model`, `demo`, `unavailable` intact.
3. **Do not report un-run checks as passing.** State exactly what you ran.
4. Unknown time stays unknown: no filling gaps from nominal FPS, no extending the last observation.
5. Schema changes go in a new Alembic migration; `backend/izge/schema_v1.py` is frozen.

More conventions are in [AGENTS.md](AGENTS.md).

## Setup

```sh
uv sync --frozen          # Python 3.14
uv run ruff check backend scripts
uv run pytest -q --ignore=backend/tests/test_integration.py --ignore=backend/tests/test_feeding_integration.py
cd frontend && npm ci && npm run build   # Node >= 22.18
```

Database-backed tests need a disposable PostgreSQL 18 (`compose.test.yaml`); never point them at a real database. SQLite or older PostgreSQL passing is not evidence for PostgreSQL 18.

## Pull requests

- Branch from `develop` unless told otherwise. One logical change per PR.
- Fill in the PR template with the real commands and results.
- Add or update tests for behaviour changes. Bug fixes should include a failing-first test.
- Update docs when commands or behaviour change.
- If an AI tool wrote a substantial part of the change, say so in the PR. You are responsible for reviewing it.

## Reviews

The maintainer aims to respond within about a week; this is a goal, not a promise. Changes touching storage, video timing, or the worker lease logic get extra scrutiny.

## Versioning and releases

No release has been published. When one is, the project will use SemVer pre-1.0 (`0.y.z`), record changes in [CHANGELOG.md](CHANGELOG.md), and tag from `main`.

## Licensing of contributions

The repository has **no license yet** (Apache-2.0 is under consideration). Until one is chosen, please do not assume contributions can be redistributed. Once a license is added, contributions will be accepted under it, with sign-off (DCO) required.

## Conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
