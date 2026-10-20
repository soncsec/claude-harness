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

Any other step left to the operator in plain text ("run this", "restart
Claude") is sent back too: Claude asks with AskUserQuestion instead, with the
steps in the question, so the UI shows that the session is waiting on them.

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
    "plain-text questions, so they may never see it. If it is a choice you can make "
    "yourself (such as whether to carry on or start the next ready issue), don't ask: "
    "make it, say in one line what you chose, and keep working. Ask the operator only "
    "about a critical design decision or something you cannot do yourself, and then "
    "with the AskUserQuestion tool (2-4 options, recommended option first). If it was "
    "not a question for the operator, restate it as a statement and stop."
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

STEPS_REASON = (
    "Your last message leaves the operator a step to do in plain text (such as running a "
    "command or restarting Claude). Nothing flags it, so they may not know this session is "
    "waiting on them. Ask with the AskUserQuestion tool instead: put the exact steps in the "
    'question, offer options such as "Done" and "Later", and carry on once they '
    "answer. A merge is the one exception: watch the pull request as "
    ".claude/skills/watch-pr/SKILL.md describes. If the steps are optional, or only answer "
    "the operator's question, and nothing waits on them, say so in one line and stop."
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
# What a pull request watcher covers.
WATCHED_STEPS = re.compile(r"\b(?:merg|approv|review)", re.IGNORECASE)

# Any other step left to the operator, in plain words.
OPERATOR_STEPS = re.compile(
    r"\b(?:you(?:['’]ll| will)? (?:need|have) to"
    r"|please (?:run|restart|exit|start|open|type|paste|create|add|set|install|click"
    r"|log ?in|sign in|go to|enable|turn on)"
    r"|(?:in|from) your (?:own )?terminal"
    r"|your (?:next )?steps|steps for you|what you need to do"
    r"|(?:exit|quit|restart|reopen) (?:claude|the session|your session|this session)"
    r"|(?:once|when|after) you (?:run|restart|install|create|add|set up|log in|finish)"
    r"|(?:once|when|after) you(?:['’]ve| have) (?:run|done|finished|restarted|installed"
    r"|created|added|set up|logged in))\b",
    re.IGNORECASE,
)

# Stands in for code, so a command shown to the operator can be spotted.
CODE = "\x1f"
# An instruction to run code: "Run `make check`", "To finish, run:" + a code block.
# The verb must start a sentence, clause or list item: "CI will run `pytest`" is fine.
COMMAND_STEP = re.compile(
    r"(?:^|[.!;:,]\s|\b(?:please|then|now|first|next|finally)\s)"
    r"\s*(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*)?"
    rf"(?:run|type|paste|enter|start)\b[^.\n{CODE}]{{0,60}}?:?\s*{CODE}",
    re.IGNORECASE | re.MULTILINE,
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


def mark_code(text: str) -> str:
    """The message with code replaced by CODE, and without links or quoted lines.

    A code block joins the line before it, so "run:" and its command stay together.
    """
    text = re.sub(r"\s*```.*?```", f" {CODE} ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", f" {CODE} ", text)
    text = re.sub(r"https?://\S+", " ", text)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))


def prose(text: str) -> str:
    """The message without code, links or quoted lines."""
    return mark_code(text).replace(CODE, " ")


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


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


def leaves_operator_steps(text: str) -> bool:
    """A step other than a watched merge, left to the operator in plain text."""
    watched = WATCHING.search(prose(text))
    for sentence in sentences(mark_code(text)):
        words = sentence.replace(CODE, " ")
        if watched and WATCHED_STEPS.search(words):
            continue  # a merge or review the session is already watching
        if OPERATOR_STEPS.search(words) or COMMAND_STEP.search(sentence):
            return True
    return False


def main() -> int:
    data = read_hook_input(fail_closed=False)
    if data.get("stop_hook_active") or data.get("permission_mode") in NO_ASK_MODES:
        return 0
    message = message_text(data.get("last_assistant_message"))
    if asks_operator(message):
        print(json.dumps({"decision": "block", "reason": REASON}))
    elif leaves_operator_steps(message):
        print(json.dumps({"decision": "block", "reason": STEPS_REASON}))
    elif leaves_operator_waiting(message):
        print(json.dumps({"decision": "block", "reason": WAIT_REASON}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a hook bug must never trap the session
        sys.exit(0)
