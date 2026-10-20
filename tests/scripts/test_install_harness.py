"""install-harness.sh: installs cleanly, re-runs safely, and never clobbers your files."""

from __future__ import annotations

import subprocess
import time
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "install-harness.sh"


def install(target: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), *args, str(target)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    target = tmp_path / "my-app"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    return target


def test_fresh_install(project):
    result = install(project, "--yes")
    assert result.returncode == 0, result.stderr
    assert (project / ".claude" / "settings.json").is_file()
    assert (project / ".claude" / "hooks" / "guard_bash.py").is_file()
    assert (project / ".github" / "workflows" / "ci.yml").is_file()
    assert (project / ".github" / "workflows" / "security.yml").is_file()
    assert (project / ".github" / "harness" / "test_integrity.py").is_file()
    assert (project / "quality-gates.toml").is_file()
    assert (project / ".claude" / "harness-version").read_text().startswith("claude-harness")
    claude_md = (project / "CLAUDE.md").read_text()
    assert claude_md.startswith("# my-app")
    assert "{{" not in claude_md
    assert "# >>> claude-harness >>>" in (project / ".gitignore").read_text()
    assert "Smoke test passed" in result.stdout


def test_rerun_changes_nothing(project):
    install(project, "--yes")
    gitignore_before = (project / ".gitignore").read_text()
    version_file = project / ".claude" / "harness-version"
    version_before = version_file.read_text()
    time.sleep(1.1)  # the install time would differ if the file were rewritten
    result = install(project, "--yes")
    assert result.returncode == 0, result.stderr
    assert (project / ".gitignore").read_text() == gitignore_before
    assert version_file.read_text() == version_before


def test_dry_run_changes_nothing(project):
    result = install(project, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "Dry run" in result.stdout
    assert not (project / ".claude").exists()
    assert not (project / "CLAUDE.md").exists()


def test_existing_claude_md_is_kept(project):
    (project / "CLAUDE.md").write_text("# My own rules\n")
    install(project, "--yes")
    assert (project / "CLAUDE.md").read_text() == "# My own rules\n"


def test_changed_settings_are_kept_without_force(project):
    install(project, "--yes")
    settings = project / ".claude" / "settings.json"
    settings.write_text('{"mine": true}\n')
    result = install(project, "--yes")
    assert settings.read_text() == '{"mine": true}\n'
    assert "Kept your .claude/settings.json" in result.stderr


def test_force_replaces_changed_settings(project):
    install(project, "--yes")
    settings = project / ".claude" / "settings.json"
    settings.write_text('{"mine": true}\n')
    install(project, "--yes", "--force")
    assert settings.read_text() == (REPO / "core" / "claude" / "settings.json").read_text()


def test_kickoff_skill_is_installed(project):
    install(project, "--yes")
    skill = project / ".claude" / "skills" / "kickoff"
    source = REPO / "core" / "claude" / "skills" / "kickoff"
    installed = {p.relative_to(skill) for p in skill.rglob("*") if p.is_file()}
    assert installed == {p.relative_to(source) for p in source.rglob("*") if p.is_file()}
    assert (skill / "SKILL.md").read_text() == (source / "SKILL.md").read_text()


def test_pr_watcher_is_installed(project):
    install(project, "--yes")
    installed = project / ".claude" / "tools" / "watch_pr.py"
    assert installed.read_text() == (REPO / "core" / "claude" / "tools" / "watch_pr.py").read_text()
    assert (project / ".claude" / "skills" / "watch-pr" / "SKILL.md").is_file()


def test_issue_forms_are_installed(project):
    install(project, "--yes")
    forms = project / ".github" / "ISSUE_TEMPLATE"
    assert {p.name for p in forms.iterdir()} == {
        p.name for p in (REPO / "core" / "github" / "ISSUE_TEMPLATE").iterdir()
    }


def test_your_issue_forms_are_kept_even_with_force(project):
    # Forms are the project's to change; updates never overwrite them.
    install(project, "--yes")
    form = project / ".github" / "ISSUE_TEMPLATE" / "feature.yml"
    form.write_text("name: Our feature form\n")
    (project / ".github" / "ISSUE_TEMPLATE" / "chore.yml").unlink()
    result = install(project, "--yes", "--force")
    assert form.read_text() == "name: Our feature form\n"
    assert "keep     .github/ISSUE_TEMPLATE/feature.yml (yours is kept)" in result.stdout
    assert (project / ".github" / "ISSUE_TEMPLATE" / "chore.yml").is_file()  # missing: added


def test_existing_gitignore_is_appended_not_replaced(project):
    (project / ".gitignore").write_text("node_modules/")  # no trailing newline
    install(project, "--yes")
    text = (project / ".gitignore").read_text()
    assert text.startswith("node_modules/\n# >>> claude-harness >>>")


def test_without_terminal_or_yes_nothing_is_installed(project):
    result = install(project)
    assert result.returncode == 0
    assert "Cancelled" in result.stdout
    assert not (project / ".claude").exists()


def test_missing_target_fails(tmp_path):
    result = install(tmp_path / "nope", "--yes")
    assert result.returncode != 0


def test_quality_gates_list_detected_python_projects(project):
    service = project / "services" / "api"
    (service / "src").mkdir(parents=True)
    (service / "uv.lock").write_text("version = 1\n")
    (project / "docs").mkdir()
    (project / "docs" / "uv.lock").write_text("version = 1\n")  # no src/: not measured
    install(project, "--yes")
    gates = tomllib.loads((project / "quality-gates.toml").read_text())
    assert gates["coverage"]["projects"] == [
        {"path": "services/api", "source": ["src"], "minimum": 80}
    ]


def test_quality_gates_are_valid_with_no_projects(project):
    install(project, "--yes")
    gates = tomllib.loads((project / "quality-gates.toml").read_text())
    assert gates["coverage"]["projects"] == []


def test_existing_quality_gates_are_kept(project):
    (project / "quality-gates.toml").write_text("# mine\n")
    install(project, "--yes")
    assert (project / "quality-gates.toml").read_text() == "# mine\n"


def add_python_project(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "uv.lock").write_text("version = 1\n")


def test_empty_coverage_list_is_filled_once_code_exists(project):
    # A new project has no code at install time; its walking skeleton adds some later.
    install(project, "--yes")
    add_python_project(project)
    plan = install(project, "--dry-run")
    assert "fill     quality-gates.toml" in plan.stdout
    result = install(project, "--yes")
    assert "Coverage gate: added" in result.stdout
    text = (project / "quality-gates.toml").read_text()
    assert tomllib.loads(text)["coverage"]["projects"] == [
        {"path": ".", "source": ["src"], "minimum": 80}
    ]
    assert text.startswith("# Quality gates")  # the explanations are kept


def test_coverage_list_with_entries_is_never_changed(project):
    add_python_project(project / "api")
    install(project, "--yes")
    gates = project / "quality-gates.toml"
    raised = gates.read_text().replace("minimum = 80", "minimum = 90")
    gates.write_text(raised)
    add_python_project(project / "worker")
    install(project, "--yes", "--force")
    assert gates.read_text() == raised
