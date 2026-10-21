"""deploy-receive.sh: the server accepts only an exact digest, and rolls back a bad release."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "core" / "deploy" / "host" / "deploy-receive.sh"
IMAGE = "ghcr.io/owner/notes"
GOOD = "a" * 64
GOOD2 = "b" * 64
BAD = "c" * 64

# Fake podman: image tags live in images.json; every call is logged.
FAKE_PODMAN = r"""#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
path = os.path.join(state, "images.json")
images = json.load(open(path)) if os.path.exists(path) else {}
with open(os.path.join(state, "log"), "a") as log:
    log.write("podman " + " ".join(sys.argv[1:])
              + f" auth={os.environ.get('REGISTRY_AUTH_FILE', '')}\n")
args = sys.argv[1:]
def resolve(ref):
    return ref if "@sha256:" in ref else images.get(ref)
if args[0] == "pull":
    if os.path.exists(os.path.join(state, "pull-fails")):
        sys.exit(125)
elif args[:2] == ["image", "exists"]:
    sys.exit(0 if args[2] in images else 1)
elif args[0] == "tag":
    images[args[2]] = resolve(args[1])
elif args[:2] == ["image", "prune"]:
    pass
json.dump(images, open(path, "w"))
"""

# Fake systemctl: a restart starts whatever localhost/notes:current points at.
FAKE_SYSTEMCTL = r"""#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
with open(os.path.join(state, "log"), "a") as log:
    log.write("systemctl " + " ".join(sys.argv[1:]) + "\n")
images = json.load(open(os.path.join(state, "images.json")))
running = os.path.join(state, "running")
if "restart" in sys.argv:
    open(running, "w").write(images.get("localhost/notes:current", ""))
elif "stop" in sys.argv and os.path.exists(running):
    os.remove(running)
"""

# Fake curl: healthy only when the running image is in FAKE_HEALTHY.
FAKE_CURL = r"""#!/usr/bin/env python3
import os, sys
state = os.environ["FAKE_STATE"]
running_file = os.path.join(state, "running")
running = open(running_file).read() if os.path.exists(running_file) else ""
healthy = [d for d in os.environ.get("FAKE_HEALTHY", "").split(",") if d]
sys.exit(0 if any(running.endswith(d) for d in healthy) else 22)
"""


def config(**overrides: str) -> str:
    values = {
        "APP": "notes",
        "IMAGE": IMAGE,
        "HEALTH_URL": "http://127.0.0.1:8000/health",
        "HEALTH_TIMEOUT": "0",
        "HEALTH_INTERVAL": "0",
    }
    values.update(overrides)
    return "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)


@pytest.fixture
def server(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("podman", FAKE_PODMAN), ("systemctl", FAKE_SYSTEMCTL), ("curl", FAKE_CURL)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    conf = tmp_path / "deploy-notes.conf"
    conf.write_text(config())

    class Server:
        def __init__(self):
            self.conf, self.state, self.home = conf, state, home

        def deploy(self, command: str | None, healthy=(), conf_path: Path | None = None):
            env = {
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(home),
                "XDG_RUNTIME_DIR": str(tmp_path),
                "FAKE_STATE": str(state),
                "FAKE_HEALTHY": ",".join(healthy),
            }
            if command is not None:
                env["SSH_ORIGINAL_COMMAND"] = command
            return subprocess.run(
                ["bash", str(SCRIPT), str(conf_path or conf)],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
                check=False,
            )

        def images(self) -> dict:
            path = state / "images.json"
            return json.loads(path.read_text()) if path.exists() else {}

        def log(self) -> str:
            path = state / "log"
            return path.read_text() if path.exists() else ""

    return Server()


def ref(digest: str, image: str = IMAGE) -> str:
    return f"{image}@sha256:{digest}"


# --- Only an exact digest of the configured image gets through -------------


@pytest.mark.parametrize(
    "command",
    [
        None,
        "",
        f"{IMAGE}:latest",
        f"{IMAGE}:{GOOD[:12]}",
        ref(GOOD, "ghcr.io/attacker/notes"),
        ref(GOOD, "ghcr.io/owner/notes-evil"),
        ref(GOOD[:63]),
        ref(GOOD.upper()),
        ref(GOOD) + " ; touch /tmp/pwned",
        ref(GOOD) + "\nrm -rf ~",
        "bash -i",
        "status",
    ],
)
def test_anything_but_an_exact_digest_is_refused(server, command):
    result = server.deploy(command, healthy=[GOOD])
    assert result.returncode == 1
    assert "refused" in result.stderr
    assert server.log() == ""  # podman never ran


def test_a_digest_of_the_configured_image_is_deployed(server):
    result = server.deploy(ref(GOOD), healthy=[GOOD])
    assert result.returncode == 0, result.stderr
    assert f"deployed {ref(GOOD)}" in result.stdout
    assert server.images()["localhost/notes:current"] == ref(GOOD)
    assert f"podman pull --quiet {ref(GOOD)}" in server.log()
    assert "systemctl --user restart notes.service" in server.log()


# --- Releases and rollback --------------------------------------------------


def test_a_healthy_release_keeps_the_last_one_for_rollback(server):
    server.deploy(ref(GOOD), healthy=[GOOD, GOOD2])
    result = server.deploy(ref(GOOD2), healthy=[GOOD, GOOD2])
    assert result.returncode == 0, result.stderr
    assert server.images()["localhost/notes:current"] == ref(GOOD2)
    assert server.images()["localhost/notes:previous"] == ref(GOOD)


def test_an_unhealthy_release_is_rolled_back(server):
    server.deploy(ref(GOOD), healthy=[GOOD])
    result = server.deploy(ref(BAD), healthy=[GOOD])
    assert result.returncode == 1
    assert "rolled back to the previous release; notes is healthy again" in result.stderr
    assert server.images()["localhost/notes:current"] == ref(GOOD)
    assert (server.state / "running").read_text() == ref(GOOD)


def test_a_rollback_that_is_still_unhealthy_says_so(server):
    server.deploy(ref(GOOD), healthy=[GOOD])
    result = server.deploy(ref(BAD), healthy=[])
    assert result.returncode == 1
    assert "still unhealthy" in result.stderr


def test_a_failed_first_deploy_stops_the_app(server):
    result = server.deploy(ref(BAD), healthy=[])
    assert result.returncode == 1
    assert "no previous release" in result.stderr
    assert "systemctl --user stop notes.service" in server.log()
    assert not (server.state / "running").exists()


def test_a_failed_pull_changes_nothing(server):
    server.deploy(ref(GOOD), healthy=[GOOD])
    (server.state / "pull-fails").touch()
    result = server.deploy(ref(GOOD2), healthy=[GOOD, GOOD2])
    assert result.returncode != 0
    assert server.images()["localhost/notes:current"] == ref(GOOD)


def test_the_registry_login_is_used_when_present(server):
    auth = server.home / ".config" / "containers" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}")
    server.deploy(ref(GOOD), healthy=[GOOD])
    assert f"auth={auth}" in server.log()


# --- The config file is data, never code ------------------------------------


def test_a_missing_config_file_is_refused(server, tmp_path):
    result = server.deploy(ref(GOOD), healthy=[GOOD], conf_path=tmp_path / "nope.conf")
    assert result.returncode == 1
    assert "config file missing" in result.stderr


@pytest.mark.parametrize(
    "overrides",
    [
        {"APP": "$(touch pwned)"},
        {"APP": "Notes"},
        {"IMAGE": "ghcr.io/owner/notes;id"},
        {"HEALTH_URL": "file:///etc/passwd"},
        {"HEALTH_TIMEOUT": "soon"},
        {"PATH": "/usr/local/evil"},
        {"HEALTH_URL": None},
    ],
)
def test_a_bad_config_is_refused(server, overrides):
    server.conf.write_text(config(**overrides))
    result = server.deploy(ref(GOOD), healthy=[GOOD])
    assert result.returncode == 1
    assert server.log() == ""
    assert not (Path(os.getcwd()) / "pwned").exists()


def test_comments_and_blank_lines_in_the_config_are_fine(server):
    server.conf.write_text("# written by setup-deploy-host.sh\n\n" + config())
    result = server.deploy(ref(GOOD), healthy=[GOOD])
    assert result.returncode == 0, result.stderr
