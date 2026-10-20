"""The /kickoff skill in core/claude/skills/kickoff: it stays wired to the rest of the harness."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "core" / "claude" / "skills" / "kickoff"
SKILL = (SKILL_DIR / "SKILL.md").read_text()
FRONTMATTER, BODY = re.match(r"---\n(.*?)\n---\n(.*)", SKILL, re.DOTALL).groups()
TEMPLATES = sorted(p for p in (SKILL_DIR / "templates").rglob("*") if p.is_file())
FEATURE_FORM = (ROOT / "core" / "github" / "ISSUE_TEMPLATE" / "feature.yml").read_text()


def meta(key: str) -> str | None:
    match = re.search(rf"^{key}: (.+)$", FRONTMATTER, re.MULTILINE)
    return match.group(1).strip() if match else None


def test_skill_is_named_kickoff():
    assert meta("name") == "kickoff"
    assert meta("description")


def test_only_the_operator_starts_kickoff():
    # Claude must not start a kickoff on its own in the middle of other work.
    assert meta("disable-model-invocation") == "true"


def test_skill_runs_in_the_main_session():
    # A forked subagent cannot use AskUserQuestion, and kickoff is all questions.
    assert meta("context") is None
    assert meta("agent") is None
    assert "AskUserQuestion" in BODY


def test_every_template_the_skill_names_exists():
    named = set(re.findall(r"templates/[\w./-]+\.\w+|templates/python/Makefile", BODY))
    assert named
    for path in named:
        assert (SKILL_DIR / path).is_file(), path


def test_every_template_is_used():
    for template in TEMPLATES:
        assert str(template.relative_to(SKILL_DIR)) in BODY, template.name


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: str(p.relative_to(SKILL_DIR)))
def test_templates_use_angle_bracket_placeholders_only(template):
    # {{...}} is the installer's syntax; templates are filled by Claude from <...>.
    assert "{{" not in template.read_text()


def test_issue_headings_match_the_feature_form():
    for heading in re.findall(r"`### ([^`]+)`", BODY):
        assert re.search(rf"^      label: {re.escape(heading)}$", FEATURE_FORM, re.MULTILINE), (
            heading
        )


def test_ready_label_only_for_approved_issues_it_created():
    assert "exactly the issues you just created from the approved list" in BODY
    assert "Never label any other issue" in BODY


def test_dependencies_go_through_uv_add():
    assert "uv add" in BODY
    additions = (SKILL_DIR / "templates" / "python" / "pyproject-additions.toml").read_text()
    parsed = tomllib.loads(additions)
    # Listing packages here would skip the operator's approval prompt.
    assert "dependency-groups" not in parsed and "project" not in parsed
    assert parsed["tool"]["uv"]["exclude-newer"] == "7 days"


def test_skeleton_makefile_has_what_ci_runs():
    makefile = (SKILL_DIR / "templates" / "python" / "Makefile").read_text()
    assert re.search(r"^check: lint test scan\b", makefile, re.MULTILINE)
    for target in ("setup", "lint", "test", "scan", "build", "run"):
        assert re.search(rf"^{target}:", makefile, re.MULTILINE), target
    recipes = [line for line in makefile.splitlines() if line.startswith(("\t", "    "))]
    assert recipes and all(line.startswith("\t") for line in recipes)  # make needs tabs
