"""The settings template's permission policy, as tests: what must stay true."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = json.loads((ROOT / "core" / "claude" / "settings.json").read_text())
_SPEC = importlib.util.spec_from_file_location("_policy", ROOT / "core/claude/hooks/_policy.py")
POLICY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(POLICY)
DENY = SETTINGS["permissions"]["deny"]
ASK = SETTINGS["permissions"]["ask"]


@pytest.mark.parametrize("rule", ["Bash(gh pr merge)", "Bash(gh pr merge *)"])
def test_agents_never_merge(rule):
    assert rule in DENY


@pytest.mark.parametrize(
    "rule", ["Bash(uv add *)", "Bash(pip install *)", "Bash(uv pip install *)"]
)
def test_new_dependencies_ask_the_operator(rule):
    assert rule in ASK


def test_web_fetches_ask_the_operator():
    # WebFetch runs outside the sandbox: an unrestricted fetch could send data anywhere.
    assert "WebFetch" in ASK


def test_agent_tokens_stay_readable_inside_the_sandbox():
    # gh and git read the tokens inside the sandbox (hclaude masks the API token
    # there). Claude Code turns Read and Edit deny rules into sandbox denies too,
    # so no rule may name the token folder: it broke gh and git push live. The
    # file hook keeps the Read, Edit and search tools away instead.
    assert not any(".cache/claude-harness" in rule for rule in DENY)
    assert not any(
        ".cache/claude-harness" in p for p in SETTINGS["sandbox"]["filesystem"]["denyRead"]
    )
    assert ".cache/claude-harness" in POLICY.SECRET_HOME_DIRS


def test_sandbox_is_strict():
    sandbox = SETTINGS["sandbox"]
    assert sandbox["enabled"] is True
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False


def test_sandbox_read_exceptions_stay_narrow():
    # The only exception to the sandbox's read rules: public CA bundles, which
    # Python TLS clients and uv's cache need (they are named *.pem, like keys).
    # Operator-approved on 2026-10-20; widening it needs the same approval.
    assert SETTINGS["sandbox"]["filesystem"].get("allowRead", []) == ["**/certifi/cacert.pem"]
