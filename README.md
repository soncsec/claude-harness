# claude-harness

A secure, repeatable harness for building software with Claude Code: guardrails,
a test-first workflow, security scanning and a gated path from a pull request to
production.

**Status:** early development. Not ready for use yet.

## Quickstart (Debian 13)

```sh
scripts/setup-debian.sh --check             # see what is installed
scripts/setup-debian.sh --with-docker       # install what is missing
scripts/install-harness.sh --dry-run ~/dev/my-project   # preview an install
scripts/install-harness.sh ~/dev/my-project             # install
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

## License

MIT. See [LICENSE](LICENSE).
