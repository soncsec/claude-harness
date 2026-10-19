"""agent-token.sh and agent-session.sh, run offline against a fake GitHub (a fake `curl`)."""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOKEN_SCRIPT = REPO / "scripts" / "agent-token.sh"
SESSION_SCRIPT = REPO / "scripts" / "agent-session.sh"
GOOD_CONFIG = (
    "CLIENT_ID=Iv23liTEST123\nINSTALLATION_ID=12345\nREPO=owner/repo\nAPP_SLUG=test-agent\n"
)

FAKE_CURL = """#!{python}
# Stands in for curl talking to GitHub. Logs each call, answers like the real API.
import json, sys
args = sys.argv[1:]
method = args[args.index("-X") + 1] if "-X" in args else "GET"
url = [a for a in args if a.startswith("http")][-1]
with open({log!r}, "a") as log:
    log.write(" ".join(args) + "\\n")
if method == "POST" and url.endswith("/access_tokens"):
    with open({counter!r}, "a+") as counter:
        counter.seek(0)
        n = len(counter.read()) + 1
        counter.write("x")
    print(json.dumps({{"token": f"ghs_FAKE{{n}}", "expires_at": "2030-01-01T00:00:00Z"}}))
    print(201)
elif method == "GET" and "/users/" in url:
    print(json.dumps({{"id": 4242}}))
    print(200)
elif method == "DELETE" and url.endswith("/installation/token"):
    print("")
    print(204)
else:
    print("{{}}")
    print(404)
"""


@pytest.fixture
def env(tmp_path: Path) -> dict:
    config = tmp_path / "config"
    config.mkdir()
    key = config / "agent-app.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    key.chmod(0o600)
    (config / "agent-app.conf").write_text(GOOD_CONFIG)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        FAKE_CURL.format(
            python=sys.executable,
            log=str(tmp_path / "curl.log"),
            counter=str(tmp_path / "counter"),
        )
    )
    curl.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CLAUDE_HARNESS_CONFIG_DIR": str(config),
        "CLAUDE_HARNESS_TOKEN_DIR": str(tmp_path / "token"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "TMP_ROOT": str(tmp_path),
    }


def run(env: dict, *args: str, script: Path = TOKEN_SCRIPT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(script), *args], capture_output=True, text=True, env=env, timeout=60, check=False
    )


def curl_log(env: dict) -> str:
    path = Path(env["TMP_ROOT"]) / "curl.log"
    return path.read_text() if path.exists() else ""


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- refresh -------------------------------------------------------------------


def test_refresh_writes_a_private_token_file(env):
    result = run(env, "refresh")
    assert result.returncode == 0, result.stderr
    token_dir = Path(env["CLAUDE_HARNESS_TOKEN_DIR"])
    hosts = token_dir / "hosts.yml"
    assert "oauth_token: ghs_FAKE1" in hosts.read_text()
    assert mode(token_dir) == 0o700
    assert mode(hosts) == 0o600
    assert (token_dir / "expires_at").read_text().strip() == "2030-01-01T00:00:00Z"


@pytest.mark.skipif(shutil.which("gh") is None, reason="gh not installed")
def test_gh_and_git_read_the_token_file(env):
    run(env, "refresh")
    gh_env = {**env, "GH_CONFIG_DIR": env["CLAUDE_HARNESS_TOKEN_DIR"]}
    answer = subprocess.run(
        ["gh", "auth", "git-credential", "get"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        env=gh_env,
        check=False,
    )
    assert "password=ghs_FAKE1" in answer.stdout


def test_second_refresh_replaces_and_revokes_the_old_token(env):
    run(env, "refresh")
    result = run(env, "refresh")
    assert result.returncode == 0, result.stderr
    hosts = Path(env["CLAUDE_HARNESS_TOKEN_DIR"]) / "hosts.yml"
    assert "ghs_FAKE2" in hosts.read_text()
    assert curl_log(env).count("-X DELETE") == 1


def test_credentials_never_appear_on_a_command_line(env):
    run(env, "refresh")
    run(env, "refresh")
    log = curl_log(env)
    assert "Bearer" not in log
    assert "ghs_FAKE" not in log


def test_token_request_is_narrowed_to_one_repo(env):
    run(env, "refresh")
    body = next(
        line.split(" -d ", 1)[1].split(" https://")[0]
        for line in curl_log(env).splitlines()
        if "access_tokens" in line
    )
    request = json.loads(body)
    assert request["repositories"] == ["repo"]
    assert "administration" not in request["permissions"]
    assert request["permissions"]["contents"] == "write"


# --- configure -------------------------------------------------------------------


def test_configure_merges_into_existing_local_settings(env, tmp_path):
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    settings = project / ".claude" / "settings.local.json"
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "env": {"KEEP": "1"}}))

    result = run(env, "configure", str(project))
    assert result.returncode == 0, result.stderr
    data = json.loads(settings.read_text())
    assert data["permissions"] == {"allow": ["Bash(ls)"]}
    assert data["env"]["KEEP"] == "1"
    assert data["env"]["GH_CONFIG_DIR"] == env["CLAUDE_HARNESS_TOKEN_DIR"]
    assert data["env"]["GIT_AUTHOR_NAME"] == "test-agent[bot]"
    assert data["env"]["GIT_AUTHOR_EMAIL"] == "4242+test-agent[bot]@users.noreply.github.com"
    assert data["env"]["GIT_CONFIG_VALUE_1"] == "!gh auth git-credential"
    assert mode(settings) == 0o600


def test_configure_dry_run_writes_nothing(env, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    result = run(env, "configure", str(project), "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "GH_CONFIG_DIR=" in result.stdout
    assert not (project / ".claude").exists()


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


def test_key_readable_by_others_is_refused(env):
    (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "agent-app.pem").chmod(0o644)
    result = run(env, "refresh")
    assert result.returncode != 0
    assert "chmod 600" in result.stderr


@pytest.mark.parametrize(
    "config,message",
    [
        (GOOD_CONFIG.replace("INSTALLATION_ID=12345", "INSTALLATION_ID=abc"), "INSTALLATION_ID"),
        (GOOD_CONFIG.replace("REPO=owner/repo", "REPO=not-a-repo"), "REPO"),
        (GOOD_CONFIG + "EXTRA=1\n", "unknown setting"),
        (GOOD_CONFIG + "this is not a setting\n", "bad line"),
    ],
)
def test_bad_config_is_rejected(env, config, message):
    (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "agent-app.conf").write_text(config)
    result = run(env, "refresh")
    assert result.returncode != 0
    assert message in result.stderr


def test_config_is_read_never_executed(env, tmp_path):
    marker = tmp_path / "pwned"
    (Path(env["CLAUDE_HARNESS_CONFIG_DIR"]) / "agent-app.conf").write_text(
        GOOD_CONFIG.replace("CLIENT_ID=Iv23liTEST123", f"CLIENT_ID=$(touch {marker})")
    )
    assert run(env, "refresh").returncode != 0
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
