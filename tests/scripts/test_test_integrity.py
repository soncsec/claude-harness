"""core/github/harness/test_integrity.py: weakened tests fail unless a maintainer approved."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import fake_github
import pytest

CHECKER = Path(__file__).resolve().parents[2] / "core" / "github" / "harness" / "test_integrity.py"

BASE_TEST = """\
import pytest

def test_add():
    assert 1 + 1 == 2
    assert 2 + 2 == 4

def test_raises():
    with pytest.raises(ZeroDivisionError):
        1 / 0
"""


def git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    (path / "tests").mkdir()
    (path / "tests" / "test_math.py").write_text(BASE_TEST)
    (path / "tests" / "test_other.py").write_text("def test_other():\n    assert True\n")
    (path / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (path / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-q"\n')
    (path / ".github").mkdir()
    (path / ".github" / "CODEOWNERS").write_text("/.claude/ @alice @org/team\n")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "base")
    git(path, "branch", "base")
    return path


def change(repo: Path, edits: dict[str, str | None]) -> None:
    for name, content in edits.items():
        target = repo / name
        if content is None:
            target.unlink()
        else:
            target.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")


def check(repo: Path, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--base", "base", "--head", "HEAD", *extra],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


@pytest.mark.parametrize(
    "edits,expected",
    [
        ({"tests/test_other.py": None}, "test file deleted"),
        (
            {"tests/test_math.py": BASE_TEST.replace("    assert 2 + 2 == 4\n", "")},
            "assertion(s) removed",
        ),
        (
            {
                "tests/test_math.py": BASE_TEST.replace(
                    "def test_add", "@pytest.mark.skip\ndef test_add"
                )
            },
            "new skip or xfail",
        ),
        ({"app.py": "def add(a, b):  # noqa: E501\n    return a + b\n"}, "new suppression"),
        ({"app.py": "def add(a, b):\n    return a + b  # pragma: no cover\n"}, "new suppression"),
        ({"app.py": "import subprocess  # nosec\n"}, "new suppression"),
        (
            {
                "pyproject.toml": "[tool.pytest.ini_options]\n"
                'addopts = "-q --deselect tests/test_math.py"\n'
            },
            "tests excluded by config",
        ),
    ],
)
def test_weakening_fails(repo, edits, expected):
    change(repo, edits)
    result = check(repo)
    assert result.returncode == 1, result.stdout
    assert expected in result.stdout
    assert "::error file=" in result.stdout


@pytest.mark.parametrize(
    "edits",
    [
        {"app.py": "def add(a, b):\n    return b + a\n"},  # ordinary code change
        {
            "tests/test_math.py": BASE_TEST + "\ndef test_more():\n    assert 3 + 3 == 6\n"
        },  # more tests
        {"tests/test_new.py": "def test_new():\n    assert 'x'\n"},  # new test file
        {"README.md": "Mentions # noqa and pytest.skip( in prose.\n"},  # not code
    ],
)
def test_ordinary_changes_pass(repo, edits):
    change(repo, edits)
    result = check(repo)
    assert result.returncode == 0, result.stdout
    assert "No weakened tests found" in result.stdout


class Approvals:
    """Run the checker against a fake GitHub that returns the given reviews."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.server = fake_github.start()

    def run(self, reviews: list[dict]) -> subprocess.CompletedProcess:
        self.server.reviews = {"/repos/owner/repo/pulls/5/reviews?per_page=100": reviews}
        env = {"GITHUB_API_URL": f"http://127.0.0.1:{self.server.server_port}", "GH_TOKEN": "x"}
        return check(self.repo, "--repo", "owner/repo", "--pr", "5", env=env)

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def approvals(repo):
    change(repo, {"tests/test_other.py": None})  # a weakening change to approve
    helper = Approvals(repo)
    yield helper
    helper.close()


def review(login: str, state: str, commit: str) -> dict:
    return {"user": {"login": login}, "state": state, "commit_id": commit}


def test_owner_approval_on_latest_commit_passes(approvals, repo):
    head = git(repo, "rev-parse", "HEAD")
    result = approvals.run([review("owner", "APPROVED", head)])
    assert result.returncode == 0, result.stdout
    assert "approved by maintainer owner" in result.stdout


def test_codeowner_user_approval_passes(approvals, repo):
    head = git(repo, "rev-parse", "HEAD")
    assert approvals.run([review("alice", "APPROVED", head)]).returncode == 0


@pytest.mark.parametrize(
    "reviews_for",
    [
        lambda head: [review("owner", "APPROVED", "0" * 40)],  # approved an older commit
        lambda head: [review("someone-else", "APPROVED", head)],  # not a maintainer
        lambda head: [review("test-agent[bot]", "APPROVED", head)],  # the agent itself
        lambda head: [review("team", "APPROVED", head)],  # @org/team is not a user
        lambda head: [review("owner", "APPROVED", head), review("owner", "DISMISSED", head)],
        lambda head: [],
    ],
    ids=["stale", "outsider", "agent", "team-name", "dismissed", "none"],
)
def test_other_approvals_do_not_count(approvals, repo, reviews_for):
    head = git(repo, "rev-parse", "HEAD")
    result = approvals.run(reviews_for(head))
    assert result.returncode == 1, result.stdout


def test_findings_are_listed_as_notices_once_approved(approvals, repo):
    head = git(repo, "rev-parse", "HEAD")
    result = approvals.run([review("owner", "APPROVED", head)])
    assert "::notice file=tests/test_other.py::test file deleted" in result.stdout
    assert "::error" not in result.stdout


def test_reviews_endpoint_is_used_with_the_token(approvals, repo):
    approvals.run([])
    (method, path, auth, _), *_ = approvals.server.calls
    assert (method, path, auth) == (
        "GET",
        "/repos/owner/repo/pulls/5/reviews?per_page=100",
        "Bearer x",
    )
