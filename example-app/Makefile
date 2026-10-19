# Shared commands: the operator, agents and CI all run these same targets.
.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv
PORT ?= 8000
# Per-project uv cache: works inside the sandbox and is not shared between projects.
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv

.PHONY: help setup lint format test scan check run

help: ## Show the available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-10s %s\n", $$1, $$2}'

setup: ## Install Python and dependencies exactly as locked in uv.lock
	$(UV) sync --frozen

lint: ## Check style, likely bugs and security patterns
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Fix formatting and import order
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

test: ## Run the tests
	$(UV) run pytest

scan: ## Security scanners (not set up yet: they arrive with CI in phase 2)
	@echo "WARNING: no security scanners are configured yet; only ruff's S rules ran in 'make lint'."

check: lint test scan ## Run everything CI runs

run: ## Run the API on http://127.0.0.1:$(PORT) (docs at /docs)
	$(UV) run uvicorn notes_api.main:create_app --factory --app-dir src \
		--host 127.0.0.1 --port $(PORT)
