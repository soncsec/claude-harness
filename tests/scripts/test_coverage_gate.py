"""core/github/harness/coverage_gate.py: coverage below the minimum fails."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "core" / "github" / "harness" / "coverage_gate.py"

spec = importlib.util.spec_from_file_location("coverage_gate", GATE)
coverage_gate = importlib.util.module_from_spec(spec)
sys.modules["coverage_gate"] = coverage_gate  # dataclasses look their module up here
spec.loader.exec_module(coverage_gate)


def gates(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "quality-gates.toml"
    path.write_text(body)
    return path


def run(gates_file: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--gates", str(gates_file), "--root", str(ROOT)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_projects_are_read(tmp_path):
    projects = coverage_gate.load_projects(
        gates(
            tmp_path,
            '[coverage]\nprojects = [\n  { path = "a", source = ["src"], minimum = 80 },\n]\n',
        )
    )
    assert projects == [coverage_gate.Project("a", ["src"], 80.0)]


def test_missing_file_means_no_gate(tmp_path):
    assert coverage_gate.load_projects(tmp_path / "nope.toml") == []


def test_minimum_must_be_a_percentage(tmp_path):
    with pytest.raises(ValueError):
        coverage_gate.load_projects(
            gates(tmp_path, '[coverage]\nprojects = [{ path = "a", minimum = 120 }]\n')
        )


def test_no_projects_passes_with_a_hint(tmp_path):
    result = run(gates(tmp_path, "[coverage]\nprojects = [\n]\n"))
    assert result.returncode == 0
    assert "No coverage gate configured" in result.stdout


def test_project_without_lockfile_fails(tmp_path):
    result = run(gates(tmp_path, '[coverage]\nprojects = [{ path = "docs", minimum = 0 }]\n'))
    assert result.returncode == 1
    assert "no uv.lock" in result.stdout


def test_example_app_meets_a_reachable_minimum(tmp_path):
    body = '[coverage]\nprojects = [{ path = "example-app", source = ["src"], minimum = 50 }]\n'
    result = run(gates(tmp_path, body))
    assert result.returncode == 0, result.stdout + result.stderr


def test_example_app_fails_an_unreachable_minimum(tmp_path):
    body = '[coverage]\nprojects = [{ path = "example-app", source = ["src"], minimum = 100 }]\n'
    result = run(gates(tmp_path, body))
    assert result.returncode == 1
    assert "below the minimum of 100%" in result.stdout
