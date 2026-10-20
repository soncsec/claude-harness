"""agent-token.sh and agent-session.sh against a local fake GitHub, using the real curl.

An earlier version passed its credential to curl in a way curl could not read,
and a fake curl hid the bug. These tests use the real curl, so the request a
real GitHub would receive is exactly what the fake server checks.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOKEN_SCRIPT = REPO / "scripts" / "agent-token.sh"
SESSION_SCRIPT = REPO / "scripts" / "agent-session.sh"
GOOD_CONFIG = "CLIENT_ID=Iv23liTEST123\nAPP_SLUG=test-agent\n"
INSTALLED = {"owner/repo", "owner/other"}  # repos the fake app is installed on
REAL_CURL = shutil.which("curl")


class FakeGitHub(BaseHTTPRequestHandler):
    """Answers the calls the scripts make, like api.github.com would."""

    def _reply(self, code: int, payload: dict | None = None) -> None:
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self) -> tuple[str, bytes]:
        auth = self.headers.get("Authorization", "")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.calls.append((self.command, self.path, auth, body))
        return auth, body

    def _has_jwt(self, auth: str) -> bool:
        return auth.startswith("Bearer ") and auth.count(".") == 2

    def do_POST(self) -> None:
        auth, _ = self._record()
        if self.path != "/app/installations/12345/access_tokens":
            return self._reply(404, {"message": "Not Found"})
        if not self._has_jwt(auth):
            return self._reply(401, {"message": "A JSON web token could not be decoded"})
        self.server.minted += 1
        self._reply(
            201, {"token": f"ghs_FAKE{self.server.minted}", "expires_at": "2030-01-01T00:00:00Z"}
        )

    def do_GET(self) -> None:
        auth, _ = self._record()
        if self.path == "/users/test-agent%5Bbot%5D":
            return self._reply(200, {"id": 4242})
        if self.path.startswith("/repos/") and self.path.endswith("/installation"):
            repo = self.path.removeprefix("/repos/").removesuffix("/installation")
            if not self._has_jwt(auth):
                return self._reply(401, {"message": "Bad credentials"})
            if repo in INSTALLED:
                return self._reply(200, {"id": 12345})
        self._reply(404, {"message": "Not Found"})

    def do_DELETE(self) -> None:
        self._record()
        self._reply(204 if self.path == "/installation/token" else 404)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def github():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    server.calls = []
    server.minted = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


@pytest.fixture
def env(tmp_path: Path, github) -> dict:
    config = tmp_path / "config"
    config.mkdir()
    key = config / "agent-app.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    key.chmod(0o600)
    (config / "agent-app.conf").write_text(GOOD_CONFIG)

    # A wrapper that records every curl command line, then runs the real curl.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "curl"
    wrapper.write_text(
        f'#!/bin/bash\nprintf "%s\\n" "$*" >> {tmp_path / "curl-args.log"}\nexec {REAL_CURL} "$@"\n'
    )
    wrapper.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_API_URL": f"http://127.0.0.1:{github.server_port}",
        "CLAUDE_HARNESS_CONFIG_DIR": str(config),
        "CLAUDE_HARNESS_TOKEN_DIR": str(tmp_path / "tokens"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "TMP_ROOT": str(tmp_path),
    }


def run(env: dict, *args: str, script: Path = TOKEN_SCRIPT, cwd: Path | None = None):
    return subprocess.run(
        [str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
        check=False,
    )


def project(tmp_path: Path, remote: str | None, name: str = "project") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    subprocess.run(["git", "init", "-q", str(directory)], check=True)
    if remote:
        subprocess.run(["git", "-C", str(directory), "remote", "add", "origin", remote], check=True)
    return directory


def register(env: dict, *repos: str) -> None:
    (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "repos").write_text("".join(f"{r}\n" for r in repos))


def token_dir(env: dict, repo: str) -> Path:
    return Path(env["CLAUDE_HARNESS_TOKEN_DIR"]) / repo


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def calls(github, method: str, path_part: str = "") -> list:
    return [c for c in github.calls if c[0] == method and path_part in c[1]]


def minted_repos(github) -> list[str]:
    return [json.loads(c[3])["repositories"][0] for c in calls(github, "POST", "access_tokens")]


# --- configure -------------------------------------------------------------------


def test_configure_registers_mints_and_wires_the_project(env, github, tmp_path):
    proj = project(tmp_path, "https://github.com/owner/repo.git")
    result = run(env, "configure", str(proj))
    assert result.returncode == 0, result.stderr

    assert "owner/repo" in (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "repos").read_text()
    hosts = token_dir(env, "owner/repo") / "hosts.yml"
    assert "oauth_token: ghs_FAKE1" in hosts.read_text()
    assert mode(hosts) == 0o600
    assert minted_repos(github) == ["repo"]

    settings = proj / ".claude" / "settings.local.json"
    data = json.loads(settings.read_text())
    assert data["env"]["GH_CONFIG_DIR"] == str(token_dir(env, "owner/repo"))
    assert data["env"]["GIT_AUTHOR_NAME"] == "test-agent[bot]"
    assert data["env"]["GIT_AUTHOR_EMAIL"] == "4242+test-agent[bot]@users.noreply.github.com"
    assert data["env"]["GIT_CONFIG_VALUE_1"] == "!gh auth git-credential"
    assert mode(settings) == 0o600


@pytest.mark.parametrize(
    "remote",
    [
        "git@github.com:owner/repo.git",
        "ssh://git@github.com/owner/repo",
        "https://someone@github.com/owner/repo/",
    ],
)
def test_configure_understands_remote_formats(env, tmp_path, remote):
    proj = project(tmp_path, remote)
    result = run(env, "configure", str(proj), "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "keep a token for owner/repo" in result.stdout


def test_configure_merges_into_existing_local_settings(env, tmp_path):
    proj = project(tmp_path, "https://github.com/owner/repo")
    (proj / ".claude").mkdir()
    settings = proj / ".claude" / "settings.local.json"
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "env": {"KEEP": "1"}}))
    assert run(env, "configure", str(proj)).returncode == 0
    data = json.loads(settings.read_text())
    assert data["permissions"] == {"allow": ["Bash(ls)"]}
    assert data["env"]["KEEP"] == "1"


def test_configure_without_a_github_remote_asks_for_repo(env, tmp_path):
    proj = project(tmp_path, "https://gitlab.com/owner/repo.git")
    result = run(env, "configure", str(proj))
    assert result.returncode != 0
    assert "--repo" in result.stderr
    assert run(env, "configure", str(proj), "--repo", "owner/repo").returncode == 0


def test_configure_when_app_not_installed_changes_nothing(env, tmp_path):
    proj = project(tmp_path, "https://github.com/owner/missing")
    result = run(env, "configure", str(proj))
    assert result.returncode != 0
    assert "github.com/settings/installations" in result.stderr
    assert not (proj / ".claude").exists()


def test_configure_dry_run_writes_nothing(env, github, tmp_path):
    proj = project(tmp_path, "https://github.com/owner/repo")
    result = run(env, "configure", str(proj), "--dry-run")
    assert result.returncode == 0, result.stderr
    assert not (proj / ".claude").exists()
    assert calls(github, "POST") == []


# --- refresh -------------------------------------------------------------------


def test_each_project_gets_its_own_one_repo_token(env, github):
    register(env, "owner/repo", "owner/other")
    result = run(env, "refresh")
    assert result.returncode == 0, result.stderr
    assert sorted(minted_repos(github)) == ["other", "repo"]
    first = (token_dir(env, "owner/repo") / "hosts.yml").read_text()
    second = (token_dir(env, "owner/other") / "hosts.yml").read_text()
    assert first != second


def test_token_request_is_narrowed(env, github):
    register(env, "owner/repo")
    run(env, "refresh")
    ((*_, body),) = calls(github, "POST")
    permissions = json.loads(body)["permissions"]
    assert permissions["contents"] == "write"
    for forbidden in ("administration", "secrets", "environments", "deployments", "workflows"):
        assert forbidden not in permissions


def test_refresh_sends_a_jwt_and_revokes_the_previous_token(env, github):
    register(env, "owner/repo")
    run(env, "refresh")
    result = run(env, "refresh")
    assert result.returncode == 0, result.stderr
    for _, _, auth, _ in calls(github, "POST"):
        assert auth.startswith("Bearer ") and auth.count(".") == 2  # the JWT really arrived
    assert "ghs_FAKE2" in (token_dir(env, "owner/repo") / "hosts.yml").read_text()
    ((_, path, auth, _),) = calls(github, "DELETE")
    assert (path, auth) == ("/installation/token", "Bearer ghs_FAKE1")


def test_one_failing_project_does_not_stop_the_others(env, github):
    register(env, "owner/missing", "owner/repo")
    result = run(env, "refresh")
    assert result.returncode != 0
    assert "owner/missing" in result.stderr
    assert (token_dir(env, "owner/repo") / "hosts.yml").exists()


def test_refresh_with_no_projects_explains_what_to_do(env):
    result = run(env, "refresh")
    assert result.returncode != 0
    assert "configure" in result.stderr


def test_old_single_repo_config_is_still_renewed(env, github):
    (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "agent-app.conf").write_text(
        GOOD_CONFIG + "INSTALLATION_ID=12345\nREPO=owner/repo\n"
    )
    result = run(env, "refresh")
    assert result.returncode == 0, result.stderr
    assert (token_dir(env, "owner/repo") / "hosts.yml").exists()


def test_credentials_never_appear_on_a_command_line(env, tmp_path):
    register(env, "owner/repo")
    run(env, "refresh")
    run(env, "refresh")
    args = (tmp_path / "curl-args.log").read_text()
    assert "Bearer" not in args
    assert "ghs_FAKE" not in args
    assert "eyJ" not in args  # no JWT either


@pytest.mark.skipif(shutil.which("gh") is None, reason="gh not installed")
def test_gh_and_git_read_the_project_token(env):
    register(env, "owner/repo")
    run(env, "refresh")
    answer = subprocess.run(
        ["gh", "auth", "git-credential", "get"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        env={**env, "GH_CONFIG_DIR": str(token_dir(env, "owner/repo"))},
        check=False,
    )
    assert "password=ghs_FAKE1" in answer.stdout


def test_unreachable_github_gives_a_clear_error(env):
    register(env, "owner/repo")
    result = run({**env, "GITHUB_API_URL": "http://127.0.0.1:9"}, "refresh")
    assert result.returncode != 0
    assert "could not reach GitHub" in result.stderr


# --- forget ----------------------------------------------------------------------


def test_forget_revokes_and_stops_renewing(env, github, tmp_path):
    proj = project(tmp_path, "https://github.com/owner/repo")
    run(env, "configure", str(proj))
    result = run(env, "forget", "owner/repo")
    assert result.returncode == 0, result.stderr
    assert not token_dir(env, "owner/repo").exists()
    assert "owner/repo" not in (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "repos").read_text()
    ((_, _, auth, _),) = calls(github, "DELETE")
    assert auth == "Bearer ghs_FAKE1"


# --- timer -----------------------------------------------------------------------


def test_install_timer_dry_run_shows_the_units(env):
    result = run(env, "install-timer", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert f"ExecStart={REPO}/scripts/agent-token.sh refresh" in result.stdout
    assert "OnCalendar=*:0/20" in result.stdout
    assert "Persistent=true" in result.stdout
    assert not (Path(env["XDG_CONFIG_HOME"]) / "systemd").exists()


# --- config and key validation -----------------------------------------------------


def b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_jwt_is_well_formed_and_signed_by_the_key(env, tmp_path):
    result = run(env, "jwt")
    assert result.returncode == 0, result.stderr
    header, payload, signature = result.stdout.strip().split(".")
    assert json.loads(b64url_decode(header)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(b64url_decode(payload))
    assert claims["iss"] == "Iv23liTEST123"
    assert claims["exp"] - claims["iat"] == 600  # GitHub allows at most 10 minutes
    assert claims["exp"] > time.time()

    key = Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "agent-app.pem"
    public_key = tmp_path / "public.pem"
    subprocess.run(
        ["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(public_key)],
        check=True,
        capture_output=True,
    )
    signature_file = tmp_path / "signature.bin"
    signature_file.write_bytes(b64url_decode(signature))
    verify = subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            str(public_key),
            "-signature",
            str(signature_file),
        ],
        input=f"{header}.{payload}".encode(),
        capture_output=True,
        check=False,
    )
    assert b"Verified OK" in verify.stdout


def test_windows_line_endings_and_quotes_are_tolerated(env):
    (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "agent-app.conf").write_text(
        'CLIENT_ID="Iv23liTEST123"\r\nAPP_SLUG = test-agent\r\n'
    )
    register(env, "owner/repo")
    result = run(env, "refresh")
    assert result.returncode == 0, result.stderr


def test_placeholder_values_are_named_in_the_error(env):
    (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "agent-app.conf").write_text(
        GOOD_CONFIG.replace("CLIENT_ID=Iv23liTEST123", "CLIENT_ID=<client id from step 1>")
    )
    result = run(env, "status")
    assert result.returncode != 0
    assert "'<client id from step 1>'" in result.stderr
    assert "starts with Iv" in result.stderr


def test_key_readable_by_others_is_refused(env):
    (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "agent-app.pem").chmod(0o644)
    result = run(env, "status")
    assert result.returncode != 0
    assert "chmod 600" in result.stderr


@pytest.mark.parametrize(
    "config,message",
    [
        (GOOD_CONFIG + "REPO=not-a-repo\n", "REPO"),
        (GOOD_CONFIG + "EXTRA=1\n", "unknown setting"),
        (GOOD_CONFIG + "this is not a setting\n", "bad line"),
    ],
)
def test_bad_config_is_rejected(env, config, message):
    (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "agent-app.conf").write_text(config)
    result = run(env, "status")
    assert result.returncode != 0
    assert message in result.stderr


def test_config_is_read_never_executed(env, tmp_path):
    marker = tmp_path / "pwned"
    (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "agent-app.conf").write_text(
        GOOD_CONFIG.replace("CLIENT_ID=Iv23liTEST123", f"CLIENT_ID=$(touch {marker})")
    )
    assert run(env, "status").returncode != 0
    assert not marker.exists()


def test_missing_config_explains_setup(env, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run({**env, "CLAUDE_HARNESS_CONFIG_DIR": str(empty)}, "refresh")
    assert result.returncode != 0
    assert "docs/setup/github-app.md" in result.stderr


def test_unknown_command_is_rejected(env):
    assert run(env, "launch-rockets").returncode == 2


# --- agent-session.sh (fallback launcher) ---------------------------------------------


def test_session_help():
    result = run({**os.environ}, "--help", script=SESSION_SCRIPT)
    assert result.returncode == 0
    assert "agent-token.sh install-timer" in result.stdout


def test_session_refuses_without_config(env, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run({**env, "CLAUDE_HARNESS_CONFIG_DIR": str(empty)}, script=SESSION_SCRIPT)
    assert result.returncode != 0
    assert "docs/setup/github-app.md" in result.stderr


def test_session_needs_a_github_project(env, tmp_path):
    proj = project(tmp_path, None)
    result = run(env, script=SESSION_SCRIPT, cwd=proj)
    assert result.returncode != 0
    assert "origin" in result.stderr
