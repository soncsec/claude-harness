# Shared commands: the operator, agents and CI all run these same targets.
.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv
TARGET ?= .

.PHONY: help setup lint format test check doctor install-harness

help: ## Show the available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-18s %s\n", $$1, $$2}'

setup: ## Install Python and the dev tools (uses uv.lock)
	$(UV) sync --frozen

lint: ## Check code style, likely bugs, security patterns and shell scripts
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run shellcheck scripts/*.sh

format: ## Fix formatting and import order
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

test: ## Run the test suite
	$(UV) run pytest

check: lint test ## Run everything CI runs

doctor: ## Check this machine has the required tools (Debian 13)
	scripts/setup-debian.sh --check

install-harness: ## Operator only: install the template into TARGET (default: this repo)
	scripts/install-harness.sh $(TARGET)
