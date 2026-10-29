.PHONY: test lint format typecheck fix install-hooks

lint:
	uv run ruff check .

format:
	uv run ruff format --check .

typecheck:
	uv run mypy

test: lint format typecheck
	uv run pytest

fix:
	uv run ruff check --fix .
	uv run ruff format .

install-hooks:
	uv run pre-commit install