#!/usr/bin/env python3
"""PreToolUse hook for Bash: block dangerous commands and commands that touch secrets.

Checks the whole command text, so commands hidden inside `sh -c "..."` or after
`&&` are caught too. Quotes and backslashes are stripped before matching, so
`s"u"do` reads as `sudo`.

This is a readable first line of defense, not a complete parser of shell
syntax. The sandbox enforces file and network limits at the OS level, and CI
enforces everything that must never ship.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _policy import OFF_LIMITS_HINT, PROPOSAL_HINT, deny, read_hook_input  # noqa: E402

# Words that end one command and start another.
SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n]")

PROTECTED = (
    r"(?:\.claude(?:/|(?=$|[\s;&|)]))|\.github/(?:workflows|harness)|\bCODEOWNERS\b"
    r"|\.pre-commit-config\.ya?ml|\.security(?:/|(?=$|[\s;&|)]))|quality-gates\.toml)"
)
PROTECTED_RX = re.compile(PROTECTED)
WRITE_COMMAND_RX = re.compile(
    r"\b(?:tee|mv|rm|rmdir|chmod|chown|truncate|dd|touch|mkdir|unzip|patch)\b"
    r"|\bsed\b[^;&|]*\s-[a-zA-Z]*i|\bperl\b[^;&|]*\s-[a-zA-Z]*i"
    r"|\bgit\s+(?:checkout|restore|apply|am|reset|rm|mv|stash)\b"
)
REDIRECT_TO_PROTECTED_RX = re.compile(r">>?\s*[^\s;&|]*" + PROTECTED)
# Copy-like commands write only to their destination, so a protected source is fine
# (e.g. backing up .claude/settings.json); a protected destination is not.
COPY_COMMANDS = {"cp", "rsync", "install", "ln"}
# Inline code can write anywhere. With a protected path in it, block it; the sandbox
# is the backstop for scripts that write files themselves.
INLINE_CODE_RX = re.compile(r"\b(?:python[0-9.]*|perl|ruby|node|php)\b[^;&|\n]*\s-[a-zA-Z]*[ce]\b")

# (name, pattern, what Claude should do instead)
RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "running as root",
        re.compile(r"\b(?:sudo|doas|pkexec)\b|(?:^|[;&|(`]|\$\()\s*su(?:\s|$)"),
        "Agents never run commands as root. If something needs root, open a needs-human "
        "issue with the exact command for the operator.",
    ),
    (
        "piping a download or text into a shell",
        re.compile(
            r"\|\s*(?:ba|z|da|k|fi)?sh\b"
            r"|\|\s*(?:python[0-9.]*|perl|ruby|node)\s*-?\s*(?:$|[;&|])"
            r"|<\(\s*(?:curl|wget)\b"
            r"|\b(?:ba|z|da)?sh\s+-c\s*\$\(\s*(?:curl|wget)\b"
            r"|\beval\b[^;&|]*\$\(\s*(?:curl|wget)\b"
        ),
        "Running downloaded code unseen is not allowed. Download the file, show it to the "
        "operator, and run it only after they approve.",
    ),
    (
        "force push",
        re.compile(
            r"\bgit\b[^;&|]*\bpush\b[^;&|]*"
            r"(?:\s--force(?![-\w])|\s-[a-zA-Z]*f[a-zA-Z]*\b|\s\+[\w/.-]+)"
        ),
        "Force-pushing rewrites shared history. Use --force-with-lease, and only on your "
        "own branch.",
    ),
    (
        "pushing to main",
        re.compile(r"\bgit\b[^;&|]*\bpush\b[^;&|]*[\s:/](?:main|master)(?=$|[\s;&|])"),
        "Never push to main. Push your branch and open a pull request.",
    ),
    (
        "skipping git hooks",
        re.compile(
            r"\bgit\b[^;&|]*\b(?:commit|push|merge|rebase|am)\b[^;&|]*"
            r"\s(?:--no-verify\b|-n(?=$|\s))"
            r"|\bcore\.hooksPath\b"
            r"|\bSKIP=\S+[^;&|]*\bgit\b"
        ),
        "Pre-commit and pre-push checks must run. Fix what they report instead of skipping them.",
    ),
    (
        "secret files or credentials",
        re.compile(
            # Backticks count as boundaries too: echo `cat .env`
            r"(?:^|[\s/=<>:(`])\.env(?:\.[\w.-]+|rc)?(?=$|[\s;&|)`])"
            r"|\.ssh(?:/|\b)|\bid_(?:rsa|ed25519|ecdsa|dsa)\b"
            r"|\.aws/|\.gnupg\b|\.kube/|\.config/gh\b|\.(?:config|cache)/claude-harness\b"
            r"|\.credentials\.json\b|\.netrc\b|\.pypirc\b|\.git-credentials\b"
            r"|\.docker/config\.json\b"
            r"|[\w/.-]+\.(?:pem|key|p12|pfx)(?=$|[\s;&|)`])"
            r"|\bgh\s+auth\s+token\b"
        ),
        OFF_LIMITS_HINT,
    ),
    (
        "approving deployments or pull requests",
        re.compile(
            # Approving = sending data to the pending_deployments endpoint. Reading it,
            # or searching docs that mention it, is fine.
            r"\b(?:gh\s+api|curl|wget)\b"
            r"(?=[^;&|\n]*pending_deployments)"
            r"(?=[^;&|\n]*(?:-X\s*POST|--method[= ]?POST|--request[= ]?POST"
            r"|\s-[fFd](?=\s)|--field|--raw-field|--input|--data))"
            r"|\bgh\s+pr\s+review\b[^;&|]*\s(?:--approve|-a)(?=$|\s)"
        ),
        "Approvals belong to the operator. Ask them to approve in GitHub.",
    ),
    (
        # GitHub no longer requires an approval for ordinary PRs, so it cannot tell the
        # operator's merge from the agent's: this rule keeps merging with the operator.
        "merging a pull request",
        re.compile(
            r"\bgh\s+pr\s+merge\b"
            r"|\b(?:gh\s+api|curl)\b(?=[^;&|\n]*pulls/\d+/merge)"
            r"(?=[^;&|\n]*(?:-X\s*PUT|--method[= ]?PUT|--request[= ]?PUT|merge_method))"
        ),
        "The operator merges pull requests. Leave the PR ready (checks passing) and tell them.",
    ),
    (
        "changing GitHub credentials or secrets",
        re.compile(r"\bgh\s+(?:secret|auth\s+(?:login|logout|refresh|switch|setup-git))\b"),
        "Credentials and repository secrets are managed by the operator.",
    ),
    (
        "publishing a package",
        re.compile(r"\b(?:uv|poetry|flit|hatch)\s+publish\b|\btwine\s+upload\b|\bnpm\s+publish\b"),
        "Releases are published by CI after operator approval, never from a dev machine.",
    ),
    (
        "privileged container",
        re.compile(
            r"\bdocker\b[^;&|]*\s(?:--privileged\b|--pid[= ]host\b|--network[= ]host\b"
            r"|-v\s+/:|--volume[= ]/:|-v\s+/var/run/docker\.sock)"
        ),
        "Containers must not get host-level access. Use the project's compose file.",
    ),
    (
        # Only running the installer counts, not mentioning it (e.g. in a commit message).
        "reinstalling the harness",
        re.compile(
            # A wrapper (bash, sh, ...) counts only where a command starts, so the
            # ".sh " ending one file name is not read as "sh" running the next file.
            r"(?:^|[\n;&|(`]|\$\()\s*(?:(?:bash|sh|exec|env|nohup|time)\s+)?"
            r"(?:[\w./~$-]*/)?(?:install-harness|new-project|update-guardrails)(?:\.sh)?"
            r"(?=$|[\s;&|)])"
            r"|\bmake\b[^;&|\n]*\b(?:install-harness|new-project|update-guardrails)\b"
        ),
        "Installing or updating guardrails is an operator step. Ask the operator to run it.",
    ),
    (
        # The sandbox shows empty placeholder files (e.g. .bashrc, quality-gates.toml)
        # over protected paths; bulk staging would commit them.
        "staging everything at once",
        re.compile(
            r"\bgit\b[^;&|\n]*\badd\b[^;&|\n]*"
            r"\s(?:--all|-[a-zA-Z]*A[a-zA-Z]*|\.|\./|:/|\*)(?=$|[\s;&|)])"
        ),
        "Stage files by name (git add path/one path/two). Inside the sandbox, empty "
        "placeholder files appear over protected paths and must never be committed.",
    ),
]

WRAPPERS = {"env", "nohup", "time", "nice", "xargs", "command", "builtin", "exec"}
DANGEROUS_RM_TARGETS = {
    "/", "/*", "~", "~/", "~/*", "$HOME", "$HOME/", "$HOME/*", "${HOME}", "${HOME}/",
    ".", "./", "*", "..", "../", "../*", ".git", ".git/",
}  # fmt: skip


def dangerous_rm(command: str) -> str | None:
    """Return the risky target of a recursive rm, or None."""
    for segment in SEGMENT_SPLIT.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        while tokens and (tokens[0] in WRAPPERS or re.match(r"^\w+=", tokens[0])):
            tokens = tokens[1:]
        if not tokens or Path(tokens[0]).name != "rm":
            continue
        args = tokens[1:]
        if "--no-preserve-root" in args:
            return "--no-preserve-root"
        recursive = any(
            arg in ("-r", "-R", "--recursive")
            or (arg.startswith("-") and not arg.startswith("--") and ("r" in arg or "R" in arg))
            for arg in args
        )
        if recursive:
            for arg in args:
                if arg in DANGEROUS_RM_TARGETS:
                    return arg
    return None


def copy_destination(segment: str) -> str | None:
    """For cp/rsync/install/ln, return the destination path; otherwise None."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return None
    while tokens and (tokens[0] in WRAPPERS or re.match(r"^\w+=", tokens[0])):
        tokens = tokens[1:]
    if not tokens or Path(tokens[0]).name not in COPY_COMMANDS:
        return None
    args = tokens[1:]
    for i, arg in enumerate(args):
        if arg in ("-t", "--target-directory") and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--target-directory="):
            return arg.split("=", 1)[1]
    positional = [arg for arg in args if not arg.startswith("-")]
    return positional[-1] if len(positional) >= 2 else None


def writes_protected(command: str) -> bool:
    for segment in SEGMENT_SPLIT.split(command):
        if not PROTECTED_RX.search(segment):
            continue
        if WRITE_COMMAND_RX.search(segment) or REDIRECT_TO_PROTECTED_RX.search(segment):
            return True
        if INLINE_CODE_RX.search(segment):
            return True
        destination = copy_destination(segment)
        if destination and PROTECTED_RX.search(destination):
            return True
    return False


GIT_COMMIT_RX = re.compile(r"\bgit\b[^\n;&|]*\bcommit\b")
MESSAGE_ARG_RX = re.compile(
    r"""(\s(?:-m|--message)(?:\s+|=))("(?:[^"\\]|\\.)*"|'[^']*'|[^\s;&|]+)"""
)
STDIN_MESSAGE_RX = re.compile(r"\s(?:-F\s*|--file[= ])-(?=\s|$)")
QUOTED_HEREDOC_RX = re.compile(r"(?<!<)<<-?\s*(['\"])(\w+)\1")
PLAIN_HEREDOC_RX = re.compile(r"(?<!<)<<-?\s*(\w+)")
EXPANSION_RX = re.compile(r"\$\(|`")
# Titles and descriptions of pull requests and issues are data, like commit
# messages. Quoted values may span lines.
GH_TEXT_ARG_RX = re.compile(
    r"""(\bgh\s+(?:pr|issue)\s+(?:create|edit|comment|review)\b[^;&|\n]*?"""
    r"""\s(?:-b|--body|-t|--title)(?:\s+|=))"""
    r"""(?!MSG(?:\s|$))"""  # skip values already dropped, so each pass finds the next flag
    r"""("(?:[^"\\]|\\.)*"|'[^']*'|[^\s;&|]+)"""
)


# A heredoc that cat or tee writes out is file content, not a command, as long as
# the line does nothing else: no pipe, no ; or &&, no $(...). Its target is still
# checked, since the line stays.
DATA_SINK_LINE_RX = re.compile(r"^\s*(?:cat|tee)(?:\s[^;&|`]*)?$")
# "$(cat <<'EOF' ... EOF)" as the whole value of a message flag on a git commit or
# gh pr/issue/release line: a quoted heredoc cannot run anything, so it is text.
CAT_HEREDOC_VALUE_START_RX = re.compile(
    r"""(\b(?:git\b[^\n;&|]*?\bcommit|gh\s+(?:pr|issue|release)\s+[a-z-]+)\b[^\n;&|]*?"""
    r"""\s(?:-m|--message|-b|--body|-t|--title|-n|--notes)(?:\s+|=))"""
    r""""\$\(\s*cat\s*<<-?\s*'(\w+)'[ \t]*\n"""
)


def drop_cat_heredoc_values(command: str) -> str:
    """Replace each "$(cat <<'EOF' ... EOF)" message value with a placeholder.

    The heredoc ends at the first line that is only the delimiter, as in bash,
    and must be followed straight away by `)"`. Otherwise it is left alone and
    checked as usual, so nothing bash would run after the heredoc can hide here.
    """
    pos = 0
    while match := CAT_HEREDOC_VALUE_START_RX.search(command, pos):
        delimiter, body_start = match.group(2), match.end()
        end = None
        for line in re.finditer(r"[^\n]*(?:\n|$)", command[body_start:]):
            if line.group().strip() == delimiter:
                end = body_start + line.end()
                break
            if not line.group():
                break
        closing = re.match(r'[ \t]*\)"', command[end:]) if end is not None else None
        if not closing:
            pos = match.end()
            continue
        command = command[: match.start()] + match.group(1) + "MSG" + command[end + closing.end() :]
        pos = match.start() + len(match.group(1)) + 3
    return command


def _drop_message(match: re.Match[str]) -> str:
    message = match.group(2)
    # Single quotes never expand; anything else is kept if it could run code.
    if not message.startswith("'") and EXPANSION_RX.search(message):
        return match.group(0)
    return match.group(1) + "MSG"


def strip_commit_messages(command: str) -> str:
    """Remove git commit message text before checking: it is data, not a command.

    Commit messages often name protected files ("update .claude/settings.json").
    A message is kept, and so still checked, when it could run code: an unquoted
    or double-quoted message, or an unquoted heredoc, containing $(...) or backticks.
    The same goes for `gh pr|issue create|edit|comment|review` titles and bodies,
    for "$(cat <<'EOF' ... EOF)" message values, and for heredocs that a line
    holding only `cat` or `tee` writes to a file.
    """
    command = drop_cat_heredoc_values(command)
    for _ in range(10):  # one flag value per pass; a command has only a few
        stripped = GH_TEXT_ARG_RX.sub(_drop_message, command, count=1)
        if stripped == command:
            break
        command = stripped
    lines = command.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        is_commit = bool(GIT_COMMIT_RX.search(line))
        if is_commit:
            line = MESSAGE_ARG_RX.sub(_drop_message, line)
        if is_commit or DATA_SINK_LINE_RX.search(line):
            heredoc = QUOTED_HEREDOC_RX.search(line) or PLAIN_HEREDOC_RX.search(line)
            reads_heredoc = STDIN_MESSAGE_RX.search(line) if is_commit else "$(" not in line
            if heredoc and reads_heredoc:
                quoted = heredoc.re is QUOTED_HEREDOC_RX
                delimiter = heredoc.group(2) if quoted else heredoc.group(1)
                end = next(
                    (j for j in range(i + 1, len(lines)) if lines[j].strip() == delimiter),
                    len(lines),
                )
                body = "\n".join(lines[i + 1 : end])
                out.append(line)
                if quoted or not EXPANSION_RX.search(body):
                    out.append(delimiter)  # message dropped
                else:
                    out.extend(lines[i + 1 : end + 1])
                i = end + 1
                continue
        out.append(line)
        i += 1
    return "\n".join(out)


def check(command: str) -> str | None:
    """Return a reason to block the command, or None to let it run."""
    joined = strip_commit_messages(command.replace("\\\n", " "))
    flat = re.sub(r"[\"'\\]", "", joined)  # s"u"do -> sudo

    target = dangerous_rm(joined)
    if target:
        return (
            f"Blocked by harness guardrail (recursive delete of {target}). "
            "Delete specific paths inside the project instead."
        )
    if writes_protected(flat):
        return f"Blocked by harness guardrail (writing a protected path). {PROPOSAL_HINT}"
    for name, pattern, advice in RULES:
        if pattern.search(flat):
            return f"Blocked by harness guardrail ({name}). {advice}"
    return None


def main() -> int:
    data = read_hook_input(fail_closed=True)
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return 0
    reason = check(command)
    if reason:
        deny(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
