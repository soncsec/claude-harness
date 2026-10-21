"""scripts/lib/claude_md.py: old harness rules leave CLAUDE.md, the project's own text stays."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "scripts" / "lib" / "claude_md.py"
LEGACY = json.loads((REPO / "scripts" / "lib" / "claude-md-legacy.json").read_text())
SECTIONS = LEGACY["sections"]
OLD_NOTE = LEGACY["comments"][0]
TEMPLATE = (REPO / "core" / "CLAUDE.template.md").read_text()
NEW_NOTE = re.search(r"<!--.*?-->", TEMPLATE, re.DOTALL).group(0)

PROJECT_PART = "## Commands\n\n`make test`\n\n"
MORE = "## More\n\n- Architecture: `docs/ARCHITECTURE.md`\n"


def old_claude_md(workflow: str | None = None) -> str:
    """A CLAUDE.md as an older install wrote it, with the project's own parts."""
    return (
        f"# my-app\n\nA notes API.\n\n{OLD_NOTE}\n\n"
        + (workflow or SECTIONS["## Workflow"][-1])
        + "\n"
        + PROJECT_PART
        + SECTIONS["## Hard rules"][-1]
        + "\n"
        + SECTIONS["## Asking the operator"][-1]
        + "\n"
        + SECTIONS["## When something fails"][-1]
        + "\n"
        + MORE
    )


def run(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), *args, str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def claude_md(tmp_path: Path) -> Path:
    return tmp_path / "CLAUDE.md"


def test_old_harness_sections_are_removed(claude_md):
    claude_md.write_text(old_claude_md())
    result = run(claude_md)
    assert result.returncode == 0, result.stderr
    expected = f"# my-app\n\nA notes API.\n\n{NEW_NOTE}\n\n{PROJECT_PART}{MORE}"
    assert claude_md.read_text() == expected
    assert result.stdout.count("removed  ") == 4


@pytest.mark.parametrize(
    ("heading", "version"),
    [(h, v) for h, versions in SECTIONS.items() for v in versions],
    ids=lambda x: x if isinstance(x, str) and x.startswith("##") else "",
)
def test_every_shipped_version_is_recognised(claude_md, heading, version):
    claude_md.write_text(f"# app\n\n{version}\n{MORE}")
    run(claude_md)
    assert claude_md.read_text() == f"# app\n\n{MORE}"


def test_an_edited_section_is_kept_and_reported(claude_md):
    edited = SECTIONS["## Workflow"][-1].replace("one small PR", "one PR")
    claude_md.write_text(old_claude_md(workflow=edited))
    result = run(claude_md)
    assert edited in claude_md.read_text()
    assert "## Hard rules" not in claude_md.read_text()
    assert "kept     ## Workflow (edited" in result.stdout


def test_a_file_without_harness_sections_is_untouched(claude_md):
    text = "# Our rules\n\n## Workflow\n\nWe pair on everything.\n\n## Style\n\nTabs."  # no newline
    claude_md.write_text(text)
    result = run(claude_md)
    assert result.returncode == 0
    assert claude_md.read_text() == text
    assert result.stdout == ""  # its own "Workflow" section is not reported as edited


def test_check_changes_nothing_and_says_whether_it_would(claude_md):
    claude_md.write_text(old_claude_md())
    assert run(claude_md, "--check").returncode == 3
    assert claude_md.read_text() == old_claude_md()
    run(claude_md)
    assert run(claude_md, "--check").returncode == 0  # nothing left to do


def test_running_twice_changes_nothing_more(claude_md):
    claude_md.write_text(old_claude_md())
    run(claude_md)
    once = claude_md.read_text()
    result = run(claude_md)
    assert claude_md.read_text() == once
    assert result.stdout == ""


def test_an_unreadable_file_is_a_failure_not_a_change(tmp_path):
    result = run(tmp_path / "missing.md", "--check")
    assert result.returncode not in (0, 3)


def test_the_template_no_longer_ships_harness_sections():
    # The legacy list is complete only while the template never gets these sections back.
    for heading in SECTIONS:
        assert not re.search(rf"^{re.escape(heading)}$", TEMPLATE, re.MULTILINE), heading
    assert NEW_NOTE not in LEGACY["comments"]
    assert ".claude/rules/harness.md" in NEW_NOTE


def test_the_rules_file_has_every_section_that_left_claude_md():
    rules = (REPO / "core" / "claude" / "rules" / "harness.md").read_text()
    for heading in SECTIONS:
        assert re.search(rf"^{re.escape(heading)}$", rules, re.MULTILINE), heading
