# Customizing the harness for your project

The defaults are strict on purpose. Loosen them only where your project needs
it, and prefer adding to a list over removing from one.

## Where each setting lives

| File | Committed? | Who changes it | Use it for |
| --- | --- | --- | --- |
| `.claude/settings.json` | Yes | Operator | Project rules everyone shares |
| `.claude/settings.local.json` | No (git-ignored) | Operator | Your personal additions on this machine |
| `~/.claude/settings.json` | No | Operator | Rules for every project on this machine |
| `.claude/rules/harness.md` | Yes | The harness (every update replaces it) | The rules every agent follows |
| `CLAUDE.md` | Yes | Operator or agent (reviewed in PR) | Project facts and rules for this project only |

Allow, ask and deny lists from all of these files are **combined**, and a deny
anywhere wins. Agents cannot edit `.claude/`; you edit those files by hand, or
apply a proposal an agent wrote in `docs/proposals/`.

## Common changes

### Adding or changing an agent rule

Write rules for one project in its `CLAUDE.md`. To change a workflow rule for
that project, add a line to `CLAUDE.md` that says what to do instead. The hard
rules cannot be changed this way; the hooks and settings enforce them anyway.
`.claude/rules/harness.md` is replaced on every update, so changes made there
are lost. To change a rule for every project, change
`core/claude/rules/harness.md` in the harness.

### Your project needs another network domain

Commands run in a sandbox that only reaches the domains listed in
`sandbox.network.allowedDomains`. If a tool fails with a network error, add its
domain:

```json
"network": {
  "allowedDomains": ["pypi.org", "files.pythonhosted.org", "github.com",
                     "api.github.com", "*.githubusercontent.com",
                     "registry.npmjs.org"]
}
```

Add exact domains rather than broad wildcards.

### A command you run all the time keeps prompting

Add it to `permissions.allow`, as narrowly as possible:

```json
"allow": ["Bash(make e2e)", "Bash(uv run alembic upgrade head)"]
```

Prefer a `make` target over many separate allow rules: it keeps the agent,
you and CI running the same commands.

### Another file should be protected

Protect it in **three** places so every layer agrees:

1. `permissions.deny` in `.claude/settings.json`: `"Edit(/config/prod.yaml)"`
2. `sandbox.filesystem.denyWrite` in the same file: `"config/prod.yaml"`
3. `PROTECTED_PATHS` in `.claude/hooks/_policy.py`: `"config/prod.yaml"`

Then add it to CODEOWNERS (once CI is set up) so changes need your review.

### Turning a hook off

Remove its entry from the `hooks` section of `.claude/settings.json`. Keep the
script so you can turn it back on. The question hook (`require_ask_tool.py`) is
the one most worth keeping: without it, plain-text questions get missed, and a
session that leaves you a step ("the PR is ready to merge", "run this script")
can sit idle with nothing telling you it is waiting.

### Choosing which PRs need your review

By default, ordinary pull requests need no approval: you look at them and click
**Merge**. Only PRs that touch the paths in `.github/CODEOWNERS` need your
approval first. The template lists the guardrail files; add any path whose
changes you always want to see, for example:

```
/deploy/          @you
/src/auth/        @you
```

To review every PR instead, add `* @you`. Agents cannot edit `CODEOWNERS`
themselves; commit the change through a pull request of your own.

### Changing the issue forms or labels

The forms in `.github/ISSUE_TEMPLATE/` are yours once installed: add fields,
reword them, and harness updates never replace them. Keep the field names
agents rely on (`Goal`, `Acceptance criteria`, `Out of scope`, and the
needs-human ones), because agents write issue bodies with those headings.
Updates add any form that is missing, so a form you delete comes back; it is
simpler to keep the set and add your own beside it.

`scripts/github-setup.sh` creates the labels in `core/github/labels.json`, and
resets their color and description on each run. Labels you add on GitHub
yourself are left alone.

### Changing how /kickoff works

Harness updates replace `.claude/skills/kickoff/`, so don't edit it in place.
Copy it to a new folder, such as `.claude/skills/our-kickoff/`, change `name:`
in its `SKILL.md` to match, and run `/our-kickoff`. Agents cannot edit skills,
so the steps they follow stay the ones you approved.

## Path syntax cheat sheet

Permission rules and sandbox paths use **different** conventions:

| Meaning | Permission rule (`Read(...)`, `Edit(...)`) | Sandbox path (`denyRead`, `denyWrite`) |
| --- | --- | --- |
| Relative to the project root | `/config/x.yaml` | `config/x.yaml` |
| In your home directory | `~/.ssh/**` | `~/.ssh` |
| Absolute | `//etc/hosts` | `/etc/hosts` |
| Anywhere below | `**/.env` | `~/**/.env` |

## Keep secrets out of reach

- Put real values in `.env` (git-ignored, unreadable to agents).
- Document every setting in `env.example` with placeholder values. The name has
  no leading dot on purpose, so agents can read it.
- Never paste secrets into a chat with an agent.
