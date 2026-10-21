#!/usr/bin/env python3
"""Take the harness's own rules out of a project's CLAUDE.md.

The harness's rules used to be sections of CLAUDE.md. That file belongs to the
project and is never replaced, so rule changes never reached existing projects.
The rules now live in .claude/rules/harness.md, which the harness keeps current.

This removes the old harness sections from CLAUDE.md, but only a section that
is word for word a version the harness shipped (claude-md-legacy.json, collected
from the history of core/CLAUDE.template.md). A section someone changed is kept,
and reported if the harness wrote the file (it has the harness's note). It also
updates that note, if it is the harness's original. Everything else in the file
is left as it is.

Usage: claude_md.py [--check] CLAUDE.md

Prints one line per change. With --check it changes nothing, and exits 3 if
something would change (1 means it failed).

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEGACY = json.loads((HERE / "claude-md-legacy.json").read_text())
TEMPLATE = HERE.parent.parent / "core" / "CLAUDE.template.md"
RULES = ".claude/rules/harness.md"


def current_note() -> str:
    match = re.search(r"<!--.*?-->", TEMPLATE.read_text(), flags=re.DOTALL)
    if match is None:
        raise SystemExit(f"error: no <!-- note --> in {TEMPLATE}")
    return match.group(0)


def migrate(text: str) -> tuple[str, list[str]]:
    """The new text, and one line for each change or kept section."""
    report = []
    note = current_note()
    from_harness = note in text or any(old in text for old in LEGACY["comments"])
    # Lossless split: each part after the first starts with a "## " heading line.
    parts = re.split(r"(?m)^(?=## )", text)
    kept = [parts[0]]
    for part in parts[1:]:
        heading = part.splitlines()[0].rstrip()
        versions = LEGACY["sections"].get(heading)
        if versions is None:
            kept.append(part)
        elif part.rstrip() + "\n" in versions:
            report.append(f"removed  {heading} (now in {RULES})")
        else:
            kept.append(part)
            if from_harness:  # otherwise it is the project's own section
                report.append(f"kept     {heading} (edited: compare with {RULES}, then remove it)")
    new = "".join(kept)
    for old in LEGACY["comments"]:
        if old in new and old != note:
            new = new.replace(old, note)
            report.append(f"updated  the note at the top (the rules are in {RULES})")
    if new != text:
        new = new.rstrip() + "\n"
    return new, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--check", action="store_true", help="change nothing; exit 3 if needed")
    args = parser.parse_args()

    text = args.path.read_text()
    new, report = migrate(text)
    for line in report:
        print(line)
    if args.check:
        return 3 if new != text else 0
    if new != text:
        args.path.write_text(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
