"""setup-deploy-host.sh: a server where CI's key can only run the deploy script."""

from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "setup-deploy-host.sh"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyDataOnlyForTests0123456789abcd"
KEY2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAISecondKeyDataOnlyForTests0123456789abcd"

# Every system command is faked: each logs its arguments. useradd makes the
# account exist for getent. id always answers as an ordinary user, so the root
# check behaves the same whoever runs the tests.
FAKE = r"""#!/usr/bin/env bash
state=$FAKE_STATE
echo "$(basename "$0") $*" >> "$state/log"
case $(basename "$0") in
  id) echo 1000 ;;
  useradd) touch "$state/user-exists" ;;
  getent)
    if [[ -e $state/user-exists ]]; then
      echo "deploy:x:1001:1001::/home/deploy:/bin/bash"
    else
      exit 2
    fi ;;
esac
exit 0
"""


@pytest.fixture
def host(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("apt-get", "useradd", "loginctl", "systemctl", "chown", "getent", "id"):
        (bin_dir / name).write_text(FAKE)
        (bin_dir / name).chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    root = tmp_path / "root"
    (root / "home" / "deploy").mkdir(parents=True)

    class Host:
        def __init__(self):
            self.root, self.state = root, state
            self.home = root / "home" / "deploy"

        def setup(self, *args: str, use_root: bool = True, base: tuple[str, ...] | None = None):
            env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_STATE": str(state)}
            if use_root:
                env["DEPLOY_HOST_ROOT"] = str(root)
            else:
                env.pop("DEPLOY_HOST_ROOT", None)
            defaults = ("--app", "notes", "--image", "ghcr.io/owner/notes", "--ci-key", KEY)
            return subprocess.run(
                [str(SCRIPT), *(defaults if base is None else base), *args],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
                check=False,
            )

        def log(self) -> str:
            path = state / "log"
            return path.read_text() if path.exists() else ""

    return Host()


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- What it sets up ---------------------------------------------------------


def test_installs_the_deploy_script_and_a_root_owned_config(host):
    result = host.setup("--port", "8080", "--health-path", "/healthz")
    assert result.returncode == 0, result.stderr
    receive = host.root / "usr/local/libexec/claude-harness/deploy-receive.sh"
    assert receive.read_text() == (REPO / "core/deploy/host/deploy-receive.sh").read_text()
    assert mode(receive) == 0o755
    conf = host.root / "etc/claude-harness/deploy-notes.conf"
    assert conf.read_text().splitlines()[:3] == [
        "APP=notes",
        "IMAGE=ghcr.io/owner/notes",
        "HEALTH_URL=http://127.0.0.1:8080/healthz",
    ]
    assert f"chown root:root {receive} {conf}" in host.log()


def test_the_ci_key_can_only_run_the_deploy_script(host):
    host.setup()
    keys = (host.home / ".ssh" / "authorized_keys").read_text().splitlines()
    assert keys == [
        'restrict,command="/usr/local/libexec/claude-harness/deploy-receive.sh '
        f'/etc/claude-harness/deploy-notes.conf" {KEY} claude-harness-deploy-notes'
    ]
    assert mode(host.home / ".ssh" / "authorized_keys") == 0o600
    assert mode(host.home / ".ssh") == 0o700


def test_a_new_key_replaces_this_apps_old_one_and_keeps_others(host):
    auth = host.home / ".ssh" / "authorized_keys"
    auth.parent.mkdir()
    auth.write_text("ssh-ed25519 AAAAOperatorsOwnKey me@laptop\n")
    host.setup()
    host.setup("--ci-key", KEY2, base=("--app", "notes", "--image", "ghcr.io/owner/notes"))
    lines = auth.read_text().splitlines()
    assert lines[0] == "ssh-ed25519 AAAAOperatorsOwnKey me@laptop"
    assert len(lines) == 2 and KEY2 in lines[1] and KEY not in lines[1]


def test_the_unit_runs_the_current_release_locked_down(host):
    host.setup("--port", "8080")
    unit = (host.home / ".config/containers/systemd/notes.container").read_text()
    assert "{{" not in unit
    assert "Image=localhost/notes:current" in unit
    assert "Pull=never" in unit
    assert "PublishPort=127.0.0.1:8080:8080" in unit
    for setting in ("ReadOnly=true", "NoNewPrivileges=true", "DropCapability=ALL"):
        assert setting in unit


def test_app_settings_are_private_and_survive_a_rerun(host):
    host.setup()
    env_file = host.home / "notes.env"
    assert mode(env_file) == 0o600
    env_file.write_text("SECRET_KEY=keep-me\n")
    host.setup()
    assert env_file.read_text() == "SECRET_KEY=keep-me\n"
    assert (host.home / "notes-data").is_dir()


def test_account_lingering_ownership_and_reload(host):
    host.setup()
    log = host.log()
    assert (
        "apt-get install -y --no-install-recommends podman uidmap passt dbus-user-session curl"
        in log
    )
    assert "useradd --create-home --shell /bin/bash deploy" in log
    assert "loginctl enable-linger deploy" in log
    assert "chown -R deploy:deploy" in log
    assert "systemctl --user --machine=deploy@ daemon-reload" in log


def test_an_existing_account_is_reused(host):
    (host.state / "user-exists").touch()
    host.setup()
    assert "useradd" not in host.log()


def test_private_images_get_a_registry_login(host, tmp_path):
    token = tmp_path / "token"
    token.write_text("ghp_readpackages\n")
    result = host.setup("--pull-user", "owner", "--pull-token-file", str(token))
    assert result.returncode == 0, result.stderr
    auth = host.home / ".config/containers/auth.json"
    assert mode(auth) == 0o600
    login = json.loads(auth.read_text())["auths"]["ghcr.io"]["auth"]
    assert base64.b64decode(login).decode() == "owner:ghp_readpackages"
    assert "ghp_readpackages" not in result.stdout + result.stderr


def test_dry_run_changes_nothing(host):
    result = host.setup("--dry-run")
    assert result.returncode == 0, result.stderr
    assert not (host.root / "etc").exists()
    assert not (host.home / ".ssh").exists()
    assert "restrict,command=" in result.stdout


def test_it_needs_root_on_a_real_server(host):
    result = host.setup(use_root=False)
    assert result.returncode == 1
    assert "run this as root" in result.stderr
    assert "apt-get" not in host.log()  # it stops before changing anything


# --- Bad input never reaches authorized_keys or the config ------------------


@pytest.mark.parametrize(
    "args",
    [
        ("--app", "Notes", "--image", "ghcr.io/owner/notes", "--ci-key", KEY),
        ("--app", "notes;id", "--image", "ghcr.io/owner/notes", "--ci-key", KEY),
        ("--app", "notes", "--image", "ghcr.io/Owner/notes", "--ci-key", KEY),
        ("--app", "notes", "--image", "ghcr.io/owner/notes", "--ci-key", "not a key"),
        ("--app", "notes", "--image", "ghcr.io/owner/notes", "--ci-key", f'command="sh" {KEY}'),
        ("--app", "notes", "--image", "ghcr.io/owner/notes", "--ci-key", 'ssh-ed25519 AAA"x'),
        ("--app", "notes", "--image", "ghcr.io/owner/notes", "--ci-key", KEY, "--port", "70000"),
        (
            "--app",
            "notes",
            "--image",
            "ghcr.io/owner/notes",
            "--ci-key",
            KEY,
            "--health-path",
            "/x y",
        ),
        (
            "--app",
            "notes",
            "--image",
            "ghcr.io/owner/notes",
            "--ci-key",
            KEY,
            "--pull-token-file",
            "/etc/hostname",
        ),
        ("--image", "ghcr.io/owner/notes", "--ci-key", KEY),
    ],
)
def test_bad_input_is_refused(host, args):
    result = host.setup(base=args)
    assert result.returncode != 0
    assert not (host.home / ".ssh").exists()
    assert not (host.root / "etc").exists()


def test_the_key_comment_is_ours_not_the_callers(host):
    host.setup("--ci-key", KEY + " evil-comment", base=("--app", "notes", "--image", "ghcr.io/o/n"))
    line = (host.home / ".ssh" / "authorized_keys").read_text()
    assert "evil-comment" not in line
