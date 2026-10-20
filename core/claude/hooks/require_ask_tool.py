#!/usr/bin/env python3
"""Stop hook: never let a session stop silently while it waits on the operator.

A question written as plain text is easy to miss: the UI does not flag it, and
the session sits waiting. When Claude is about to stop and its last message
ends with a question, this hook sends it back once to re-ask with the
AskUserQuestion tool, which the UI does flag (and which can trigger a push
notification).

The same goes for a message that leaves the operator a step ("the PR is ready
to merge") with nothing watching for it: Claude is sent back to watch the pull
request (and notify the operator) or to ask.

It is a heuristic. A false alarm costs one extra turn, nothing more.
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

WAIT_REASON = (
    "Your last message leaves the operator a step (such as merging a pull request), but "
    "nothing tells them this session is waiting, and nothing wakes it up afterwards. For a "
    "pull request, watch it as .claude/skills/watch-pr/SKILL.md describes: run "
    "`python3 .claude/tools/watch_pr.py <number>` in the background (`--until merged` once "
    "the checks pass), send a PushNotification that it is ready to merge, and say you are "
    "watching it. For anything else, ask with the AskUserQuestion tool. If nothing waits "
    "on the operator, restate your message without that and stop."
)

# Permission modes where AskUserQuestion is unavailable or auto-denied.
NO_ASK_MODES = {"dontAsk"}

QUESTION_PHRASES = re.compile(
    r"\b(?:let me know|would you like|do you want|want me to|should I|shall I"
    r"|can you confirm|could you confirm|please confirm|which (?:option|one) (?:do|would|should)"
    r"|your call|up to you|tell me (?:when|once))\b",
    re.IGNORECASE,
)

# A step left to the operator, anywhere in the message.
WAITING_PHRASES = re.compile(
    r"\b(?:ready (?:to merge|for (?:your )?review)"
    r"|once (?:it(?:['’]s| is) |you(?:['’]ve| have) )?merged"
    r"|(?:after|when) you merge|(?:after|when) you(?:['’]ve| have) merged"
    r"|waiting (?:for|on) you|over to you)\b",
    re.IGNORECASE,
)
# The agent says it is watching, so the session will wake up by itself.
WATCHING = re.compile(r"\bwatching\b", re.IGNORECASE)


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


def prose(text: str) -> str:
    """The message without code, links or quoted lines."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))


def closing_text(text: str) -> str:
    """The last paragraph of the prose."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", prose(text)) if p.strip()]
    return paragraphs[-1] if paragraphs else ""


def asks_operator(text: str) -> bool:
    tail = closing_text(text)
    if not tail:
        return False
    if re.search(r"\?[*_)\]\s]*$", tail, flags=re.MULTILINE):
        return True
    return bool(QUESTION_PHRASES.search(tail))


def leaves_operator_waiting(text: str) -> bool:
    body = prose(text)
    return bool(WAITING_PHRASES.search(body)) and not WATCHING.search(body)


def main() -> int:
    data = read_hook_input(fail_closed=False)
    if data.get("stop_hook_active") or data.get("permission_mode") in NO_ASK_MODES:
        return 0
    message = message_text(data.get("last_assistant_message"))
    if asks_operator(message):
        print(json.dumps({"decision": "block", "reason": REASON}))
    elif leaves_operator_waiting(message):
        print(json.dumps({"decision": "block", "reason": WAIT_REASON}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a hook bug must never trap the session
        sys.exit(0)
