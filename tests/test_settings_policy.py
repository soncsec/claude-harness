"""The settings template's permission policy, as tests: what must stay true."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SETTINGS = json.loads(
    (Path(__file__).resolve().parents[1] / "core" / "claude" / "settings.json").read_text()
)
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


@pytest.mark.parametrize(
    "rule", ["Read(~/.cache/claude-harness/**)", "Edit(~/.cache/claude-harness/**)"]
)
def test_agent_tokens_are_closed_to_the_unsandboxed_tools(rule):
    # gh and git read the tokens inside the sandbox (hclaude masks the API token
    # there), so the folder must stay sandbox-readable; only the tools are denied.
    assert rule in DENY
    assert not any(
        ".cache/claude-harness" in p for p in SETTINGS["sandbox"]["filesystem"]["denyRead"]
    )


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
