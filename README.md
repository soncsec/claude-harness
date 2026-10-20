# claude-harness

A secure, repeatable harness for building software with Claude Code: guardrails,
a test-first workflow, security scanning and a gated path from a pull request to
production.

**Status:** early development. Not ready for use yet.

## Quickstart (Debian 13)

```sh
scripts/setup-debian.sh --check             # see what is installed
scripts/setup-debian.sh --with-docker       # install what is missing
scripts/new-project.sh --create <you>/<name> ~/dev/<name>   # new repo, set up end to end
scripts/new-project.sh ~/dev/<existing-project>             # or an existing GitHub project
```

Then follow [docs/getting-started.md](docs/getting-started.md): review, tailor
([docs/customizing.md](docs/customizing.md)) and prove it works
([docs/red-team-checklist.md](docs/red-team-checklist.md)).

## What you get

- **Guardrails:** permissions, an OS-level sandbox and hooks that block dangerous
  commands, keep agents away from secrets and stop them editing their own rules.
- **Questions you will not miss:** agents must ask with the question prompt, not
  in plain text.
- **Reminders:** each session starts with what is waiting on you in GitHub.
- **Its own GitHub identity:** agents push branches and open pull requests as a
  GitHub App bot with short-lived, one-repo tokens; you merge.
  See [docs/setup/github-app.md](docs/setup/github-app.md).
- **CI on every pull request:** tests, secret scanning, code and dependency
  scans, all required before merge. See [docs/ci.md](docs/ci.md).
- **A work queue in GitHub:** issue forms for features, bugs, chores and
  operator tasks, each with testable acceptance criteria, plus the `ready`,
  `needs-human` and `security` labels.

## Security

Found a way around a guardrail? Please report it privately; see
[SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
