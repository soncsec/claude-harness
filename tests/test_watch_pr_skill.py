"""The watch-pr skill and the watch_pr.py tool it drives stay in step."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT / "core" / "claude" / "skills" / "watch-pr" / "SKILL.md").read_text()
FRONTMATTER, BODY = re.match(r"---\n(.*?)\n---\n(.*)", SKILL, re.DOTALL).groups()
TOOL = (ROOT / "core" / "claude" / "tools" / "watch_pr.py").read_text()
TEMPLATE = (ROOT / "core" / "CLAUDE.template.md").read_text()


def meta(key: str) -> str | None:
    match = re.search(rf"^{key}: (.+)$", FRONTMATTER, re.MULTILINE)
    return match.group(1).strip() if match else None


def emitted_prefixes() -> set[str]:
    return set(re.findall(r'return \(?\s*f?"([a-z-]+): ', TOOL))


def documented_prefixes() -> set[str]:
    docstring = TOOL.split('"""')[1]
    return set(re.findall(r"^ {4}([a-z-]+): ", docstring, re.MULTILINE))


def test_claude_can_load_it_on_its_own():
    # Claude must reach for it after every pull request, unprompted.
    assert meta("name") == "watch-pr"
    assert "pull request" in meta("description")
    assert meta("disable-model-invocation") is None
    assert meta("context") is None  # a forked subagent could not notify or ask


def test_the_tool_documents_every_line_it_prints():
    assert emitted_prefixes() == documented_prefixes()
    assert {"passed", "failed", "merged", "closed"} <= emitted_prefixes()


def test_the_skill_says_what_to_do_with_every_line():
    for prefix in emitted_prefixes():
        assert f"`{prefix}:`" in BODY, f"the skill never handles `{prefix}:`"


def test_the_skill_runs_the_installed_tool_in_the_background():
    assert "python3 .claude/tools/watch_pr.py <number>" in BODY
    assert "--until merged" in BODY
    assert "run_in_background" in BODY
    assert "PushNotification" in BODY


def test_project_rules_point_at_the_skill():
    assert "`watch-pr` skill" in TEMPLATE
