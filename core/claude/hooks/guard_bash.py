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
    r"(?:\.claude(?:/|(?=$|[\s;&|)]))|\.github/workflows|\bCODEOWNERS\b"
    r"|\.pre-commit-config\.ya?ml|\.security(?:/|(?=$|[\s;&|)]))|quality-gates\.toml)"
)
PROTECTED_RX = re.compile(PROTECTED)
WRITE_COMMAND_RX = re.compile(
    r"\b(?:tee|mv|cp|rm|rmdir|chmod|chown|ln|truncate|dd|install|rsync|touch|mkdir|unzip|patch)\b"
    r"|\bsed\b[^;&|]*\s-[a-zA-Z]*i|\bperl\b[^;&|]*\s-[a-zA-Z]*i"
    r"|\bgit\s+(?:checkout|restore|apply|am|reset|rm|mv|stash)\b"
)
REDIRECT_TO_PROTECTED_RX = re.compile(r">>?\s*[^\s;&|]*" + PROTECTED)

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
            r"(?:^|[\s/=<>:(])\.env(?:\.[\w.-]+|rc)?(?=$|[\s;&|)])"
            r"|\.ssh(?:/|\b)|\bid_(?:rsa|ed25519|ecdsa|dsa)\b"
            r"|\.aws/|\.gnupg\b|\.kube/|\.config/gh\b"
            r"|\.credentials\.json\b|\.netrc\b|\.pypirc\b|\.git-credentials\b"
            r"|\.docker/config\.json\b"
            r"|[\w/.-]+\.(?:pem|key|p12|pfx)(?=$|[\s;&|)])"
            r"|\bgh\s+auth\s+token\b"
        ),
        OFF_LIMITS_HINT,
    ),
    (
        "approving deployments or pull requests",
        re.compile(r"pending_deployments|\bgh\s+pr\s+review\b[^;&|]*\s(?:--approve|-a)(?=$|\s)"),
        "Approvals belong to the operator. Ask them to approve in GitHub.",
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
        "reinstalling the harness",
        re.compile(r"\binstall-harness\b"),
        "Installing or updating guardrails is an operator step. Ask the operator to run it.",
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


def writes_protected(command: str) -> bool:
    for segment in SEGMENT_SPLIT.split(command):
        if not PROTECTED_RX.search(segment):
            continue
        if WRITE_COMMAND_RX.search(segment) or REDIRECT_TO_PROTECTED_RX.search(segment):
            return True
    return False


def check(command: str) -> str | None:
    """Return a reason to block the command, or None to let it run."""
    joined = command.replace("\\\n", " ")
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
