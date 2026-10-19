#!/usr/bin/env python3
"""Stop hook: make sure questions for the operator go through AskUserQuestion.

A question written as plain text is easy to miss: the UI does not flag it, and
the session sits waiting. When Claude is about to stop and its last message
ends with a question, this hook sends it back once to re-ask with the
AskUserQuestion tool, which the UI does flag (and which can trigger a push
notification).

It is a heuristic. A rhetorical question costs one extra turn, nothing more.
It fails open: if anything goes wrong, Claude is allowed to stop.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _policy import read_hook_input  # noqa: E402

REASON = (
    "Your last message asks the operator something in plain text. The UI does not flag "
    "plain-text questions, so they may never see it. Re-ask it with the AskUserQuestion "
    "tool (2-4 options, recommended option first). If it was not a question for the "
    "operator, restate it as a statement and stop."
)

# Permission modes where AskUserQuestion is unavailable or auto-denied.
NO_ASK_MODES = {"dontAsk"}

QUESTION_PHRASES = re.compile(
    r"\b(?:let me know|would you like|do you want|want me to|should I|shall I"
    r"|can you confirm|could you confirm|please confirm|which (?:option|one) (?:do|would|should)"
    r"|your call|up to you)\b",
    re.IGNORECASE,
)


def message_text(value: object) -> str:
    """last_assistant_message may be a string or a list of content blocks."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def closing_text(text: str) -> str:
    """The last paragraph, without code, links or quoted lines."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    lines = [line for line in text.splitlines() if not line.lstrip().startswith(">")]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
    return paragraphs[-1] if paragraphs else ""


def asks_operator(text: str) -> bool:
    tail = closing_text(text)
    if not tail:
        return False
    if re.search(r"\?[*_)\]\s]*$", tail, flags=re.MULTILINE):
        return True
    return bool(QUESTION_PHRASES.search(tail))


def main() -> int:
    data = read_hook_input(fail_closed=False)
    if data.get("stop_hook_active") or data.get("permission_mode") in NO_ASK_MODES:
        return 0
    if asks_operator(message_text(data.get("last_assistant_message"))):
        print(json.dumps({"decision": "block", "reason": REASON}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a hook bug must never trap the session
        sys.exit(0)
