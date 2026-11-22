.PHONY: verify test
verify:
	./scripts/verify.sh
test:
	uv run pytest -q --ignore=backend/tests/test_integration.py --ignore=backend/tests/test_feeding_integration.py
