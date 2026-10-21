"""setup-deploy-runner.sh: a checksum-checked runner that only takes one repo's deploy jobs."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "setup-deploy-runner.sh"
PINNED_SHA = re.search(r"^RUNNER_SHA256=([0-9a-f]{64})$", SCRIPT.read_text(), re.M).group(1)
REG_CODE = "AABBCCDDEEFF00112233445566"

# System commands, faked: each logs its arguments. tar "unpacks" a config.sh and
# svc.sh that log too; runuser runs the command after `--`.
FAKE = r"""#!/usr/bin/env bash
state=$FAKE_STATE
name=$(basename "$0")
echo "$name $*" >> "$state/log"
case $name in
  id) echo 1000 ;;
  hostname) echo rdmdevel ;;
  getent) exit 2 ;;
  curl)
    while (( $# )); do [[ $1 == -o ]] && { echo fake-tarball > "$2"; }; shift; done ;;
  sha256sum) echo "${FAKE_SHA:-PINNED}  $1" ;;
  tar)
    dir=${@: -1}
    for tool in config.sh svc.sh; do
      printf '#!/usr/bin/env bash\necho "%s $*" >> "%s/log"\n' "$tool" "$state" > "$dir/$tool"
      chmod +x "$dir/$tool"
    done ;;
  runuser)
    while [[ $1 != -- ]]; do shift; done; shift
    exec "$@" ;;
esac
exit 0
"""


@pytest.fixture
def machine(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = FAKE.replace("PINNED", PINNED_SHA)
    for name in (
        "apt-get",
        "getent",
        "useradd",
        "curl",
        "sha256sum",
        "tar",
        "chown",
        "runuser",
        "hostname",
        "id",
    ):
        (bin_dir / name).write_text(fake)
        (bin_dir / name).chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    root = tmp_path / "root"
    root.mkdir()

    class Machine:
        def __init__(self):
            self.root, self.state = root, state
            self.dir = root / "opt/claude-harness-runners/owner-app"

        def setup(
            self,
            *args: str,
            token: str | None = REG_CODE,
            use_root: bool = True,
            env: dict | None = None,
        ):
            run_env = {
                **os.environ,
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "FAKE_STATE": str(state),
                **(env or {}),
            }
            if use_root:
                run_env["DEPLOY_HOST_ROOT"] = str(root)
            base = ("--repo", "Owner/App", "--label", "lab-deploy")
            return subprocess.run(
                [str(SCRIPT), *(args or base)],
                input=token if token is not None else "",
                capture_output=True,
                text=True,
                env=run_env,
                timeout=30,
                check=False,
            )

        def log(self) -> str:
            path = state / "log"
            return path.read_text() if path.exists() else ""

    return Machine()


def ran(machine, command: str) -> bool:
    """Whether a logged call started with this command."""
    return any(line.split()[0] == command for line in machine.log().splitlines() if line)


def test_registers_a_runner_that_only_takes_the_deploy_label(machine):
    result = machine.setup()
    assert result.returncode == 0, result.stderr
    log = machine.log()
    assert (
        "config.sh --unattended --url https://github.com/Owner/App "
        f"--token {REG_CODE} --labels lab-deploy --no-default-labels "
        "--name rdmdevel-lab-deploy --replace"
    ) in log
    assert "runuser -u gh-runner -- ./config.sh" in log


def test_runs_as_a_locked_down_system_account(machine):
    machine.setup()
    log = machine.log()
    assert (
        "useradd --system --create-home --home-dir /var/lib/gh-runner "
        "--shell /usr/sbin/nologin gh-runner"
    ) in log
    assert f"chown -R gh-runner:gh-runner {machine.dir}" in log
    assert "svc.sh install gh-runner" in log and "svc.sh start" in log


def test_downloads_the_pinned_release_over_https_only(machine):
    machine.setup()
    url = re.search(r"curl .* (https://\S+)", machine.log()).group(1)
    assert re.fullmatch(
        r"https://github\.com/actions/runner/releases/download/v([\d.]+)/"
        r"actions-runner-linux-x64-\1\.tar\.gz",
        url,
    )
    assert "--proto =https" in machine.log()
    assert not (machine.dir / "runner.tar.gz").exists()


def test_a_download_that_fails_its_checksum_installs_nothing(machine):
    result = machine.setup(env={"FAKE_SHA": "0" * 64})
    assert result.returncode == 1
    assert "does not match its pinned checksum" in result.stderr
    assert not ran(machine, "tar") and not ran(machine, "config.sh")
    assert not (machine.dir / "runner.tar.gz").exists()


def test_the_token_never_appears_in_its_output(machine):
    result = machine.setup()
    assert REG_CODE not in result.stdout + result.stderr


@pytest.mark.parametrize("token", ["", "not a token!", "short"])
def test_a_missing_or_malformed_token_is_refused(machine, token):
    result = machine.setup(token=token)
    assert result.returncode == 1
    assert "registration token" in result.stderr
    assert not ran(machine, "curl")


def test_an_existing_runner_is_left_alone(machine):
    machine.dir.mkdir(parents=True)
    (machine.dir / ".runner").write_text("{}")
    result = machine.setup()
    assert result.returncode == 0, result.stderr
    assert "already registered" in result.stdout
    assert not ran(machine, "curl")


def test_dry_run_downloads_nothing_and_needs_no_token(machine):
    result = machine.setup("--repo", "Owner/App", "--label", "lab-deploy", "--dry-run", token="")
    assert result.returncode == 0, result.stderr
    assert "--token ***" in result.stdout
    assert not ran(machine, "curl")


def test_it_needs_root_on_a_real_machine(machine):
    result = machine.setup(use_root=False)
    assert result.returncode == 1
    assert "run this as root" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ("--repo", "Owner/App", "--label", "Lab Deploy"),
        ("--repo", "Owner/App;id", "--label", "lab-deploy"),
        ("--repo", "Owner/App", "--label", "lab-deploy", "--user", "Root!"),
        ("--label", "lab-deploy"),
    ],
)
def test_bad_arguments_are_refused(machine, args):
    result = machine.setup(*args)
    assert result.returncode != 0
    assert machine.log() == ""
