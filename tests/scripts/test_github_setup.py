"""github-setup.sh and the ruleset files it applies: the rules keep merging with the owner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "github-setup.sh"
MAIN = json.loads((REPO / "core" / "github" / "ruleset-main.json").read_text())
TAGS = json.loads((REPO / "core" / "github" / "ruleset-tags.json").read_text())

ADMIN_ROLE = 5  # GitHub's id for the "Repository admin" role


def rule(ruleset: dict, rule_type: str) -> dict | None:
    return next((r for r in ruleset["rules"] if r["type"] == rule_type), None)


def test_main_ruleset_covers_the_default_branch():
    assert MAIN["target"] == "branch"
    assert MAIN["enforcement"] == "active"
    assert MAIN["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]


@pytest.mark.parametrize("rule_type", ["deletion", "non_fast_forward", "pull_request"])
def test_main_ruleset_blocks_direct_changes(rule_type):
    # pull_request means every change to main goes through a PR: no direct pushes.
    assert rule(MAIN, rule_type) is not None


def test_approved_prs_can_merge_without_bypass():
    # Operator's choice (2026-10-20): no "update" (restrict updates) rule, so an
    # approved PR merges without the admin bypass. The bot may then merge a PR
    # the owner approved, but never one they have not.
    assert rule(MAIN, "update") is None


def test_main_ruleset_needs_owner_review_only_for_guardrail_files():
    # Operator's choice (2026-10-20): ordinary PRs merge without an approval (the
    # operator clicks Merge); Code Owner review still guards the guardrail files.
    params = rule(MAIN, "pull_request")["parameters"]
    assert params["required_approving_review_count"] == 0
    assert params["require_code_owner_review"] is True
    assert params["dismiss_stale_reviews_on_push"] is True


CODEOWNERS = (REPO / "core" / "github" / "CODEOWNERS.template").read_text()


@pytest.mark.parametrize(
    "path",
    ["/.claude/", "/.github/", "/.security/", "/quality-gates.toml", "/.pre-commit-config.yaml"],
)
def test_codeowners_template_covers_guardrail_files(path):
    assert any(
        line.split()[0] == path
        for line in CODEOWNERS.splitlines()
        if line.strip() and not line.startswith("#")
    )


def test_codeowners_template_has_no_catch_all():
    # A "*" line would require review of every PR again.
    entries = [
        line.split()[0]
        for line in CODEOWNERS.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert "*" not in entries
    assert all(
        line.endswith("@{{OWNER}}")
        for line in CODEOWNERS.splitlines()
        if line.strip() and not line.startswith("#")
    )


def test_only_admins_bypass_and_only_through_pull_requests():
    assert MAIN["bypass_actors"] == [
        {"actor_id": ADMIN_ROLE, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}
    ]


def test_tag_ruleset_locks_all_tags():
    assert TAGS["target"] == "tag"
    assert TAGS["conditions"]["ref_name"]["include"] == ["~ALL"]
    assert {r["type"] for r in TAGS["rules"]} == {"creation", "update", "deletion"}
    assert [a["actor_id"] for a in TAGS["bypass_actors"]] == [ADMIN_ROLE]


def test_help():
    result = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "protect-main" in result.stdout


def test_bad_repo_argument_is_rejected():
    result = subprocess.run(
        [str(SCRIPT), "not a repo"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
