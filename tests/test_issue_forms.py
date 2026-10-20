"""The issue forms and labels in core/github: the fields agents rely on are always there.

GitHub validates the forms only once they are pushed, so these tests check the
parts the harness depends on without a YAML library.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = ROOT / "core" / "github" / "ISSUE_TEMPLATE"
FORMS = {p.stem: p.read_text() for p in sorted(FORMS_DIR.glob("*.yml")) if p.stem != "config"}
LABELS = json.loads((ROOT / "core" / "github" / "labels.json").read_text())
LABEL_NAMES = [label["name"] for label in LABELS]

# Field labels each form must keep: kickoff and agents write issue bodies with
# these headings, and acceptance criteria become the first tests.
REQUIRED_FIELDS = {
    "feature": ["Goal", "Acceptance criteria", "Out of scope"],
    "bug": ["Steps to reproduce", "Expected and actual behavior", "Acceptance criteria"],
    "chore": ["What and why", "Acceptance criteria"],
    "needs-human": ["What the operator must do", "Exact steps", "What it unblocks"],
}


def fields(text: str) -> list[str]:
    """Labels of the form's fields (indented six spaces; checkbox options are deeper)."""
    return re.findall(r"^      label: (.+)$", text, re.MULTILINE)


def test_there_is_a_form_for_each_kind_of_work():
    assert set(FORMS) == set(REQUIRED_FIELDS)


@pytest.mark.parametrize("name", sorted(REQUIRED_FIELDS))
def test_form_keeps_the_fields_agents_rely_on(name):
    assert all(field in fields(FORMS[name]) for field in REQUIRED_FIELDS[name])


@pytest.mark.parametrize("name", sorted(REQUIRED_FIELDS))
def test_form_fields_are_required(name):
    text = FORMS[name]
    textareas = len(re.findall(r"^  - type: (?:textarea|input)$", text, re.MULTILINE))
    required = len(re.findall(r"^      required: true$", text, re.MULTILINE))
    assert textareas == required > 0


@pytest.mark.parametrize("name", sorted(REQUIRED_FIELDS))
def test_form_has_the_top_level_keys_github_needs(name):
    for key in ("name", "description", "labels", "body"):
        assert re.search(rf"^{key}:", FORMS[name], re.MULTILINE), f"{name}: no {key}"


@pytest.mark.parametrize("name", sorted(REQUIRED_FIELDS))
def test_form_labels_exist(name):
    labels = json.loads(re.search(r"^labels: (\[.*\])$", FORMS[name], re.MULTILINE).group(1))
    assert labels and set(labels) <= set(LABEL_NAMES)


@pytest.mark.parametrize("name", sorted(REQUIRED_FIELDS))
def test_form_ids_are_unique(name):
    ids = re.findall(r"^    id: (\S+)$", FORMS[name], re.MULTILINE)
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path", sorted(FORMS_DIR.glob("*.yml")), ids=lambda p: p.name)
def test_form_indentation_is_plain_spaces(path):
    for line in path.read_text().splitlines():
        assert "\t" not in line
        assert (len(line) - len(line.lstrip(" "))) % 2 == 0, line


def test_blank_issues_are_off():
    config = (FORMS_DIR / "config.yml").read_text()
    assert re.search(r"^blank_issues_enabled: false$", config, re.MULTILINE)


def test_the_workflow_labels_exist():
    assert {"ready", "needs-human", "security"} <= set(LABEL_NAMES)


def test_labels_are_valid_for_github():
    assert len(LABEL_NAMES) == len(set(LABEL_NAMES))
    for label in LABELS:
        assert re.fullmatch(r"[0-9a-f]{6}", label["color"]), label
        assert 0 < len(label["description"]) <= 100, label  # GitHub's limit
        assert "\t" not in label["description"]  # github-setup.sh splits on tabs
