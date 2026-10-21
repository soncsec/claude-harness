# Shared commands: the operator, agents and CI all run these same targets.
.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv
TARGET ?= .
# Per-project uv cache: the sandbox blocks the shared ~/.cache/uv, and a shared
# cache would let one project's agent plant packages another project installs.
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
# Tools run with `uv tool run` (pip-audit, zizmor) live in the project too:
# ~/.local/share is read-only inside the sandbox.
export UV_TOOL_DIR ?= $(CURDIR)/.cache/uv-tools
export UV_TOOL_BIN_DIR ?= $(CURDIR)/.cache/uv-tools/bin
# Python TLS clients default to certifi's cacert.pem, which the sandbox hides along
# with every *.pem file (private keys often use that extension). Point them at the
# system's CA bundle instead; it is public and not hidden.
SYSTEM_CA := $(firstword $(wildcard /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt))
ifneq ($(SYSTEM_CA),)
export REQUESTS_CA_BUNDLE ?= $(SYSTEM_CA)
export SSL_CERT_FILE ?= $(SYSTEM_CA)
endif
# Same versions as core/github/workflows/security.yml (a test keeps them equal).
PIP_AUDIT ?= pip-audit==2.10.1
ZIZMOR ?= zizmor==1.30.1

.PHONY: help setup lint format test scan check example-check doctor install-harness update-guardrails

help: ## Show the available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-18s %s\n", $$1, $$2}'

setup: ## Install Python and the dev tools (uses uv.lock)
	$(UV) sync --frozen

lint: ## Check code style, likely bugs, security patterns and shell scripts
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run shellcheck -x scripts/*.sh scripts/lib/*.sh core/deploy/host/*.sh
	$(UV) tool run --from $(ZIZMOR) zizmor --offline --min-severity medium \
		core/github/workflows/*.yml core/deploy/*.yml $(wildcard .github/workflows/*.yml)

scan: ## Check locked dependencies for known vulnerabilities (CI also scans secrets and code)
	@mkdir -p .cache
	$(UV) export --frozen --format requirements-txt --no-emit-project -o .cache/requirements.txt
	$(UV) tool run --from $(PIP_AUDIT) pip-audit -r .cache/requirements.txt \
		--disable-pip --require-hashes --strict --cache-dir .cache/pip-audit

format: ## Fix formatting and import order
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

test: ## Run the test suite
	$(UV) run pytest

example-check: ## Run the example app's checks
	$(MAKE) -C example-app check

check: lint test scan example-check ## Run everything CI's `check` job runs

doctor: ## Check this machine has the required tools (Debian 13)
	scripts/setup-debian.sh --check

install-harness: ## Operator only: install the template into TARGET (default: this repo)
	scripts/install-harness.sh $(TARGET)

update-guardrails: ## Operator only: update TARGET's guardrails via a PR, CI and merge
	scripts/update-guardrails.sh $(TARGET)
