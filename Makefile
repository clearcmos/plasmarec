# Development tasks. Set up once with:
#   uv venv --system-site-packages --python /usr/bin/python3 && uv sync
.PHONY: check lint format typecheck test coverage

check: lint typecheck coverage

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

typecheck:
	uv run mypy

test:
	uv run pytest -q

coverage:
	uv run coverage run -m pytest -q
	uv run coverage report --fail-under=85
