"""core/deploy/deploy.yml: staging on merge, production only after the operator approves."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "core" / "deploy" / "deploy.yml").read_text()
SECURITY = (ROOT / "core" / "github" / "workflows" / "security.yml").read_text()
CONTAINERFILE = (
    ROOT / "core" / "claude" / "skills" / "kickoff" / "templates" / "python" / "Containerfile"
).read_text()
MAKEFILE = (
    ROOT / "core" / "claude" / "skills" / "kickoff" / "templates" / "python" / "Makefile"
).read_text()


def job(name: str) -> str:
    """The text of one job, up to the next job."""
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [\w-]+:\n|\Z)", TEXT, re.MULTILINE | re.DOTALL)
    assert match, f"no job {name}"
    return match.group(1)


def test_runs_only_on_pushes_to_main():
    # A pull request must never deploy, or see the deploy secrets.
    trigger = re.search(r"^on:\n(.*?)\n\S", TEXT, re.MULTILINE | re.DOTALL).group(1)
    assert trigger.split() == ["push:", "branches:", "[main]"]


def test_production_waits_for_staging_and_the_approval_gate():
    assert "environment: production" in job("production")
    assert re.search(r"needs: \[build, staging\]", job("production"))
    assert "environment: staging" in job("staging")
    assert "needs: build" in job("staging")


def test_one_deploy_at_a_time_and_never_cancelled_midway():
    assert re.search(r"^concurrency:\n  group: deploy\n  cancel-in-progress: false", TEXT, re.M)


def test_no_permissions_by_default_and_only_build_can_push_images():
    assert re.search(r"^permissions: \{\}$", TEXT, re.MULTILINE)
    assert "packages: write" in job("build")
    for name in ("staging", "production"):
        assert "permissions: {}" in job(name)
        assert "GITHUB_TOKEN" not in job(name)


def test_every_action_is_pinned_and_checkouts_keep_no_credentials():
    for ref, comment in re.findall(r"uses:\s*(\S+)(.*)", TEXT):
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"not pinned: {ref}"
        assert re.search(r"#\s*v\d", comment)
    assert TEXT.count("persist-credentials: false") == TEXT.count("uses: actions/checkout@")


def test_no_expressions_inside_run_blocks():
    # Values reach scripts through env, never pasted into the script (template injection).
    blocks = re.findall(r"run: \|\n((?:\s{10,}.*\n?)+)", TEXT)
    assert len(blocks) == TEXT.count("run: ") == 6
    for block in blocks:
        assert "${{" not in block


def test_ssh_checks_the_servers_host_key():
    for name in ("staging", "production"):
        body = job(name)
        assert "StrictHostKeyChecking=yes" in body
        assert "UserKnownHostsFile=" in body
        assert "BatchMode=yes" in body
        assert "secrets.DEPLOY_SSH_KEY" in body and "secrets.DEPLOY_KNOWN_HOSTS" in body


def test_deploys_send_the_exact_digest_built():
    assert 'echo "image=$image@$(cat "$RUNNER_TEMP/digest")"' in job("build")
    for name in ("staging", "production"):
        assert "IMAGE: ${{ needs.build.outputs.image }}" in job(name)


def test_dependencies_are_audited_again_with_ci_s_pip_audit():
    version = re.search(r"pip-audit==([\d.]+)", SECURITY).group(1)
    assert f"pip-audit=={version}" in job("build")


def test_the_python_containerfile_runs_unprivileged_from_locked_hashes():
    assert re.search(r"^USER 10001$", CONTAINERFILE, re.MULTILINE)
    assert "--require-hashes -r requirements.txt" in CONTAINERFILE
    assert ":latest" not in CONTAINERFILE
    assert "DATA_DIR=/data" in CONTAINERFILE


def test_the_python_makefile_prepares_the_build_context():
    assert re.search(r"^container-context:", MAKEFILE, re.MULTILINE)
    assert "--no-dev" in MAKEFILE and "requirements.txt" in MAKEFILE


def test_deploy_jobs_can_run_inside_the_operators_network():
    # Servers on a private network need a runner there (setup-deploy.sh --runner).
    for name in ("staging", "production"):
        assert "runs-on: ${{ vars.DEPLOY_RUNNER || 'ubuntu-latest' }}" in job(name)
    # Building stays on GitHub's runners: it needs podman, and no secrets of yours.
    assert "runs-on: ubuntu-latest" in job("build")


def test_one_image_runs_on_intel_and_arm_servers():
    # The first live deploy reached an ARM server with an amd64-only image.
    build = job("build")
    assert "PLATFORMS: ${{ vars.DEPLOY_PLATFORMS || 'linux/amd64,linux/arm64' }}" in build
    assert '--platform "$PLATFORMS" --manifest "$image:$SHA"' in build
    assert "podman manifest push --all" in build
    assert "qemu-user-static" in build
