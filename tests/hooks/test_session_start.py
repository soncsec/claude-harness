"""session_start.py: reminders come from GitHub; failures stay silent."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ISSUES = [
    {
        "number": 5,
        "title": "Create staging VM",
        "url": "https://github.com/o/r/issues/5",
        "createdAt": "2026-01-01T00:00:00Z",
    }
]
PRS = [
    {
        "number": 7,
        "title": "Add login",
        "url": "https://github.com/o/r/pull/7",
        "createdAt": "2026-01-02T00:00:00Z",
        "isDraft": False,
        "reviewDecision": "REVIEW_REQUIRED",
    },
    {
        "number": 8,
        "title": "Already approved",
        "url": "u",
        "createdAt": "2026-01-02T00:00:00Z",
        "isDraft": False,
        "reviewDecision": "APPROVED",
    },
    {
        "number": 9,
        "title": "Draft work",
        "url": "u",
        "createdAt": "2026-01-02T00:00:00Z",
        "isDraft": True,
        "reviewDecision": "",
    },
]


def fake_gh(bin_dir: Path, *, exit_code: int = 0) -> None:
    """Install a fake `gh` that answers issue and pr list calls."""
    script = bin_dir / "gh"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"issues = {json.dumps(json.dumps(ISSUES))}\n"
        f"prs = {json.dumps(json.dumps(PRS))}\n"
        "print(issues if sys.argv[1] == 'issue' else prs)\n"
        f"sys.exit({exit_code})\n"
    )
    script.chmod(0o755)


def test_lists_waiting_items(run_hook, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh(bin_dir)
    result = run_hook("session_start.py", {"source": "startup"}, env={"PATH": str(bin_dir)})
    assert result.returncode == 0
    assert "needs-human #5: Create staging VM" in result.stdout
    assert "PR #7 awaiting review" in result.stdout
    assert "#8" not in result.stdout  # approved
    assert "#9" not in result.stdout  # draft


def test_silent_without_gh(run_hook, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run_hook("session_start.py", {"source": "startup"}, env={"PATH": str(empty)})
    assert result.returncode == 0
    assert result.stdout == ""


def test_silent_when_gh_fails(run_hook, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh(bin_dir, exit_code=1)
    result = run_hook("session_start.py", {"source": "startup"}, env={"PATH": str(bin_dir)})
    assert result.returncode == 0
    assert result.stdout == ""
