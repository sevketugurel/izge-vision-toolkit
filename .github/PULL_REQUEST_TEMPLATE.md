## What and why

<!-- One paragraph. Link the issue if there is one. -->

## How it was verified

<!-- Paste the commands you ran and their real result. Do not tick boxes for checks you did not run. -->

- [ ] `uv run ruff check backend scripts`
- [ ] `uv run pytest -q --ignore=backend/tests/test_integration.py --ignore=backend/tests/test_feeding_integration.py`
- [ ] PostgreSQL 18 integration tests (only if the change touches the database, worker, or API)
- [ ] `npm run build` in `frontend/` (only if the change touches the frontend)

## Data and safety

- [ ] No farm footage, images, model weights, credentials, camera addresses, or personal data added.
- [ ] Result origins (`manual`, `model`, `demo`, `unavailable`) are preserved. No demo or mock output is presented as a real result.
- [ ] Docs updated if behaviour or commands changed.

## AI assistance

<!-- If an AI tool wrote a substantial part of this change, say so. You remain responsible for reviewing it. -->
