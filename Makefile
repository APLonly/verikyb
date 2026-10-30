# Install make:
#   Windows : winget install ezwinports.make   (or: choco install make / scoop install make)
#   macOS   : xcode-select --install           (or: brew install make, then run as gmake)
#   Linux   : sudo apt install make            (Debian/Ubuntu)
#             sudo dnf install make            (Fedora/RHEL)

# Install uv (also downloads Python and creates .venv for you):
#   Windows : winget install astral-sh.uv
#             (or: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex")
#   macOS   : brew install uv
#             (or: curl -LsSf https://astral.sh/uv/install.sh | sh)
#   Linux   : curl -LsSf https://astral.sh/uv/install.sh | sh
#   Any OS  : pip install uv
#
# First-time setup order:  install make -> install uv -> make setup

.DEFAULT_GOAL := help
.PHONY: help setup test fix

help: ## List the targets
	@uv run python scripts/help.py $(MAKEFILE_LIST)

setup: ## Install the pinned dependencies and the git hooks
	uv sync --locked
	uv run pre-commit install

test: ## Lint, type-check and run the offline test suite
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pytest

fix: ## Auto-fix lint issues and reformat
	uv run ruff check --fix .
	uv run ruff format .