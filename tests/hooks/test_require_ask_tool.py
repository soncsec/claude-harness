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
        "Added this to the script:\n\n```\nread -p 'Continue?' answer\n```",
        "> Should we keep it?\n\nWe kept it.",
        "See https://example.com/page?id=3",
        "",
    ],
)
def test_statement_is_allowed(run_hook, message):
    result = run_hook("require_ask_tool.py", stop(message))
    assert not result.blocked_stop, f"should be allowed: {message!r}"


@pytest.mark.parametrize(
    "message",
    [
        "Kickoff PR #3 is open: https://github.com/o/r/pull/3\n\nIt's ready to merge.",
        "PR #4 is ready for your review.\n\nWhat's in it:\n- README\n- ROADMAP",
        "Docs are pushed. Once it's merged, I'll build the skeleton.",
        "Over to you: merge #5 when CI is green.",
        "I'll start phase 4 when you merge the PR.",
        "Tell me when the PR is merged.",
    ],
)
def test_leaving_the_operator_a_step_is_sent_back(run_hook, message):
    result = run_hook("require_ask_tool.py", stop(message))
    assert result.blocked_stop, f"should be sent back: {message!r}"
    reason = result.output["reason"]
    assert "watch_pr.py" in reason or "AskUserQuestion" in reason


def test_wait_reason_says_how_to_watch(run_hook):
    result = run_hook("require_ask_tool.py", stop("PR #3 is ready to merge."))
    reason = result.output["reason"]
    assert "watch_pr.py" in reason and "PushNotification" in reason and "--until merged" in reason


@pytest.mark.parametrize(
    "message",
    [
        "PR #3 is ready to merge. I'm watching it and will carry on once it's merged.",
        "Both PRs are merged and all checks pass.",
        "After you merged #26, I pulled main; all tests pass.",
        "The label text is `ready to merge`.",
        "> Once it's merged, run the update.\n\nDone; nothing else is needed.",
    ],
)
def test_finished_or_watched_work_is_allowed(run_hook, message):
    result = run_hook("require_ask_tool.py", stop(message))
    assert not result.blocked_stop, f"should be allowed: {message!r}"


def test_question_reason_says_to_decide_small_things(run_hook):
    result = run_hook("require_ask_tool.py", stop("Done. Should I start the next issue?"))
    reason = result.output["reason"]
    assert "critical design decision" in reason and "keep working" in reason
    assert "ready issue" not in reason  # agents no longer wait for a `ready` label


STEPS_LEFT_IN_PLAIN_TEXT = [
    "Merged.\n\n**Your steps:**\n\n1. Update this repo:\n   ```\n   scripts/x.sh\n   ```\n"
    "2. Restart the trial.\n\nI'll fix the script in a later PR.",
    "Done. To finish, run:\n\n```\nscripts/deploy.sh\n```",
    "Run this:\n\n```\nread -p 'Continue?' answer\n```",
    "1. Run `scripts/deploy.sh ~/dev/app`\n2. Restart Claude.",
    "The skill is installed. You'll need to restart Claude to load it.",
    "Please run `make doctor` and paste the output.",
    "In your own terminal: `rm canary.txt`",
    "Once you've created the VM, I'll set up the deploy.",
    "I'm watching PR #3. Meanwhile, you need to restart the session.",
    "Fixed. Next, type `y` at the prompt.",
    # Mentions a merge, but nothing is watching it: still a step left to the operator.
    "Run `scripts/deploy.sh`; it merges the pull request for you.",
    "You'll need to approve PR #3 before it can merge.",
]


@pytest.mark.parametrize("message", STEPS_LEFT_IN_PLAIN_TEXT)
def test_steps_left_in_plain_text_are_sent_back(run_hook, message):
    result = run_hook("require_ask_tool.py", stop(message))
    assert result.blocked_stop, f"should be sent back: {message!r}"
    reason = result.output["reason"]
    assert "AskUserQuestion" in reason and "steps in the" in reason


def test_steps_reason_points_merges_at_the_watcher(run_hook):
    result = run_hook("require_ask_tool.py", stop("Please run `make doctor`."))
    assert "watch-pr" in result.output["reason"]


@pytest.mark.parametrize(
    "message",
    [
        "Ran `make check`: all 432 tests pass.",
        "CI will run `pytest` on every push.",
        "I'll run `make check` again after the next change.",
        "After you ran the update, I pulled main; all tests pass.",
        "You don't need to do anything else.",
        "The hook runs `ruff` and `pytest`.",
        "> Please run the update.\n\nDone; nothing else is needed.",
        "Nothing waits on you; the update merged #30.",
        "PR #3 is ready to merge. You'll need to approve it first, since it touches "
        "guardrails. I'm watching it.",
    ],
)
def test_reports_and_watched_reviews_are_allowed(run_hook, message):
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
