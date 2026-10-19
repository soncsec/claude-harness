"""agent-session.sh: config is validated (never executed) and the app JWT is correctly signed."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "agent-session.sh"
GOOD_CONFIG = (
    "CLIENT_ID=Iv23liTEST123\nINSTALLATION_ID=12345\nREPO=owner/repo\nAPP_SLUG=test-agent\n"
)


def run(config_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_HARNESS_CONFIG_DIR": str(config_dir)}
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60, check=False
    )


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "harness-config"
    directory.mkdir()
    key = directory / "agent-app.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    key.chmod(0o600)
    (directory / "agent-app.conf").write_text(GOOD_CONFIG)
    return directory


def b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_jwt_is_well_formed_and_signed_by_the_key(config_dir, tmp_path):
    result = run(config_dir, "--print-jwt")
    assert result.returncode == 0, result.stderr
    header, payload, signature = result.stdout.strip().split(".")

    assert json.loads(b64url_decode(header)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(b64url_decode(payload))
    assert claims["iss"] == "Iv23liTEST123"
    assert claims["exp"] - claims["iat"] == 600  # GitHub allows at most 10 minutes
    assert claims["exp"] > time.time()

    public_key = tmp_path / "public.pem"
    subprocess.run(
        [
            "openssl",
            "rsa",
            "-in",
            str(config_dir / "agent-app.pem"),
            "-pubout",
            "-out",
            str(public_key),
        ],
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


def test_key_readable_by_others_is_refused(config_dir):
    (config_dir / "agent-app.pem").chmod(0o644)
    result = run(config_dir, "--print-jwt")
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
def test_bad_config_is_rejected(config_dir, config, message):
    (config_dir / "agent-app.conf").write_text(config)
    result = run(config_dir, "--print-jwt")
    assert result.returncode != 0
    assert message in result.stderr


def test_config_is_read_never_executed(config_dir, tmp_path):
    marker = tmp_path / "pwned"
    (config_dir / "agent-app.conf").write_text(
        GOOD_CONFIG.replace("CLIENT_ID=Iv23liTEST123", f"CLIENT_ID=$(touch {marker})")
    )
    result = run(config_dir, "--print-jwt")
    assert result.returncode != 0
    assert not marker.exists()


def test_missing_config_explains_setup(tmp_path):
    result = run(tmp_path, "--print-jwt")
    assert result.returncode != 0
    assert "docs/setup/github-app.md" in result.stderr


def test_help():
    result = run(Path("/nonexistent"), "--help")
    assert result.returncode == 0
    assert "agent-session.sh" in result.stdout
