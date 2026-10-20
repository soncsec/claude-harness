#!/usr/bin/env python3
"""Fail when a project's test coverage is below the minimum in quality-gates.toml.

quality-gates.toml (operator-owned) lists the Python projects to measure:

    [coverage]
    projects = [
      { path = "example-app", source = ["src"], minimum = 80 },
    ]

Each project's tests run under coverage.py with `uv run`, using its own uv.lock.
CI reads quality-gates.toml and this file from the target branch, never from the
pull request, so a pull request cannot lower its own bar.

Standard library only (coverage.py itself is fetched by uv, pinned below).
Installed by claude-harness (source: core/github/harness/coverage_gate.py).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

COVERAGE = "coverage==7.16.2"


@dataclass
class Project:
    path: str
    source: list[str]
    minimum: float


def load_projects(gates_file: Path) -> list[Project]:
    if not gates_file.exists():
        return []
    data = tomllib.loads(gates_file.read_text())
    projects = []
    for entry in data.get("coverage", {}).get("projects", []):
        minimum = float(entry["minimum"])
        if not 0 <= minimum <= 100:
            raise ValueError(f"coverage minimum for {entry['path']} must be 0-100")
        projects.append(Project(entry["path"], list(entry.get("source", ["."])), minimum))
    return projects


def measure(project: Project, root: Path) -> bool:
    folder = root / project.path
    if not (folder / "uv.lock").exists():
        print(f"::error::{project.path}: no uv.lock; cannot run its tests reproducibly")
        return False
    sources = ",".join(project.source)
    uv = ["uv", "run", "--frozen", "--with", COVERAGE]
    print(f"::group::{project.path}: tests under coverage (minimum {project.minimum:g}%)")
    ran = subprocess.run(  # noqa: S603 - fixed command, values from the operator's file
        [*uv, "coverage", "run", f"--source={sources}", "-m", "pytest", "-q"],
        cwd=folder,
        check=False,
    )
    print("::endgroup::")
    if ran.returncode != 0:
        print(f"::error::{project.path}: tests failed under coverage")
        return False
    report = subprocess.run(  # noqa: S603
        [*uv, "coverage", "report", f"--fail-under={project.minimum}"], cwd=folder, check=False
    )
    if report.returncode != 0:
        print(f"::error::{project.path}: coverage is below the minimum of {project.minimum:g}%")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gates", type=Path, default=Path("quality-gates.toml"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()

    projects = load_projects(args.gates)
    if not projects:
        print("No coverage gate configured (add projects to [coverage] in quality-gates.toml).")
        return 0
    results = [measure(project, args.root) for project in projects]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
