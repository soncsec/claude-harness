"""The CI workflow templates in core/github/workflows: supply-chain and permission rules."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / "core" / "github" / "workflows").glob("*.yml"))
TEXT = {path.name: path.read_text() for path in WORKFLOWS}
RULESET = json.loads((ROOT / "core" / "github" / "ruleset-main.json").read_text())


def test_there_are_workflows():
    assert {"ci.yml", "security.yml"} <= set(TEXT)


@pytest.mark.parametrize("name", sorted(TEXT))
def test_every_action_is_pinned_to_a_commit(name):
    uses = re.findall(r"uses:\s*(\S+)(.*)", TEXT[name])
    assert uses
    for ref, comment in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"not pinned: {ref}"
        assert re.search(r"#\s*v\d", comment), f"pin without its version noted: {ref}"


@pytest.mark.parametrize("name", sorted(TEXT))
def test_no_permissions_by_default(name):
    assert re.search(r"^permissions: \{\}$", TEXT[name], re.MULTILINE)


@pytest.mark.parametrize("name", sorted(TEXT))
def test_every_job_declares_its_own_permissions(name):
    jobs = len(re.findall(r"^    runs-on:", TEXT[name], re.MULTILINE))
    declared = len(re.findall(r"^    permissions:", TEXT[name], re.MULTILINE))
    assert jobs == declared


@pytest.mark.parametrize("name", sorted(TEXT))
def test_checkouts_do_not_keep_credentials(name):
    checkouts = TEXT[name].count("uses: actions/checkout@")
    assert TEXT[name].count("persist-credentials: false") == checkouts


def job_names() -> set[str]:
    return {m for text in TEXT.values() for m in re.findall(r"^    name: ([\w-]+)$", text, re.M)}


def required_checks() -> set[str]:
    rule = next(r for r in RULESET["rules"] if r["type"] == "required_status_checks")
    return {c["context"] for c in rule["parameters"]["required_status_checks"]}


def test_required_checks_exist_as_jobs():
    # A required check that no job reports would block every merge.
    assert required_checks() <= job_names()


def test_every_unconditional_job_is_required():
    # dependency-review only runs on public repos, so it cannot be required everywhere.
    assert job_names() - required_checks() == {"dependency-review"}


@pytest.mark.parametrize(
    "makefile", [ROOT / "Makefile", ROOT / "example-app" / "Makefile"], ids=["root", "example-app"]
)
def test_local_tool_versions_match_ci(makefile):
    security = TEXT["security.yml"]
    text = makefile.read_text()
    for tool in ("pip-audit", "zizmor"):
        local = re.search(rf"{tool}==([\d.]+)", text)
        if local:
            assert f"{tool}=={local.group(1)}" in security, f"{tool} differs from CI"
