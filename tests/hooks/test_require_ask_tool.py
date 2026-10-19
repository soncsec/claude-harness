"""require_ask_tool.py: plain-text questions are sent back to be asked with the tool."""

from __future__ import annotations

import pytest


def stop(message, **extra) -> dict:
    return {"hook_event_name": "Stop", "last_assistant_message": message, **extra}


@pytest.mark.parametrize(
    "message",
    [
        "Done. Should I also add tests?",
        "All tests pass.\n\nWant me to open the PR?",
        "I updated the config. Let me know if you want changes.",
        "**Ready to merge?**",
        "Two options:\n\n1. Keep it\n2. Drop it\n\nWhich one would you prefer?",
    ],
)
def test_question_is_sent_back(run_hook, message):
    result = run_hook("require_ask_tool.py", stop(message))
    assert result.blocked_stop, f"should be sent back: {message!r}"
    assert "AskUserQuestion" in result.output["reason"]


@pytest.mark.parametrize(
    "message",
    [
        "Done. Tests pass.",
        "Why did it fail? A missing import.\n\nFixed it; all tests pass.",
        "Run this:\n\n```\nread -p 'Continue?' answer\n```",
        "> Should we keep it?\n\nWe kept it.",
        "See https://example.com/page?id=3",
        "",
    ],
)
def test_statement_is_allowed(run_hook, message):
    result = run_hook("require_ask_tool.py", stop(message))
    assert not result.blocked_stop, f"should be allowed: {message!r}"


def test_content_block_list_is_understood(run_hook):
    message = [{"type": "text", "text": "Finished. Should I deploy to staging?"}]
    result = run_hook("require_ask_tool.py", stop(message))
    assert result.blocked_stop


def test_second_stop_is_never_blocked(run_hook):
    result = run_hook("require_ask_tool.py", stop("Should I?", stop_hook_active=True))
    assert not result.blocked_stop


def test_dont_ask_mode_is_left_alone(run_hook):
    result = run_hook("require_ask_tool.py", stop("Should I?", permission_mode="dontAsk"))
    assert not result.blocked_stop


def test_broken_input_fails_open(run_hook):
    result = run_hook("require_ask_tool.py", "not json")
    assert result.returncode == 0
    assert not result.stdout.strip()
