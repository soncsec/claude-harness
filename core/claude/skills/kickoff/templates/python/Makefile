# Shared commands: the operator, agents and CI all run these same targets.
.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv
# Per-project uv cache: works inside the sandbox and is not shared between projects.
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
# Tools run with `uv tool run` live in the project too (read-only home in the sandbox).
export UV_TOOL_DIR ?= $(CURDIR)/.cache/uv-tools
export UV_TOOL_BIN_DIR ?= $(CURDIR)/.cache/uv-tools/bin
PIP_AUDIT ?= pip-audit==2.10.1

.PHONY: help setup lint format test scan check build run container-context

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

scan: ## Check locked dependencies for known vulnerabilities (CI also scans secrets and code)
	@mkdir -p .cache
	$(UV) export --frozen --format requirements-txt --no-emit-project -o .cache/requirements.txt
	$(UV) tool run --from $(PIP_AUDIT) pip-audit -r .cache/requirements.txt \
		--disable-pip --require-hashes --strict --cache-dir .cache/pip-audit

check: lint test scan ## Run everything CI runs

build: ## Build the package into dist/
	$(UV) build

container-context: ## Prepare dist/ and requirements.txt for the Containerfile (the deploy workflow runs this)
	rm -rf dist
	$(UV) build --wheel -o dist
	$(UV) export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.txt

run: ## Run it locally
	<the command that starts the app, for example:
	$(UV) run uvicorn <package>.main:create_app --factory --host 127.0.0.1 --port 8000>
