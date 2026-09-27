.PHONY: check test run

# Lint and byte-compile. ruff comes from requirements-dev.txt.
check:
	ruff check .
	python -m compileall -q .

# pytest, when a tests/ folder exists.
test:
	@if [ -d tests ]; then python -m pytest -q; else echo "no tests/ folder yet"; fi

run:
	docker compose up -d --build
