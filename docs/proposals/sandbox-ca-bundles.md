# Proposal: let sandboxed commands read public CA certificate bundles

**Status:** accepted by the operator on 2026-10-20 and included in the phase 2b
guardrail batch. `tests/test_settings_policy.py` keeps the exception this narrow.

## The problem

The sandbox hides every `*.pem` file from commands (`Read(**/*.pem)` in
`permissions.deny` feeds the sandbox's read rules), because private keys often
use that extension. But Python's standard bundle of *public* certificate
authorities, `certifi/cacert.pem`, uses it too. Any Python tool that uses
`requests` (pip-audit, many API clients) then fails inside the sandbox with
`SSLError(PermissionError(13, 'Permission denied'))`.

The Makefiles now work around it for `make scan` by pointing those tools at the
system bundle (`/etc/ssl/certs/ca-certificates.crt`). Commands an agent runs
outside `make` still fail.

## What should change

In `core/claude/settings.json`, add an exception inside the sandbox's read rules:

```json
"sandbox": {
  "filesystem": {
    "allowRead": ["**/certifi/cacert.pem"]
  }
}
```

## Risk

Only files named `cacert.pem` inside a `certifi` folder become readable. Those
hold public certificate authorities, nothing secret. A private key would have to
be renamed into that exact path to be exposed, which an agent could only do if
it could already read the key.

## How to verify

Not confirmed from the documentation: whether `allowRead` accepts this glob
form. After applying it, in a new session:

- `uvx --from pip-audit==2.10.1 pip-audit --help` then a real audit outside
  `make` succeeds
- Red-team checklist item 2 (`cat .env`) and item 5 (reading a key) are still
  blocked
- `cat some/private.pem` (create a dummy one) is still blocked

## How to apply

Edit `core/claude/settings.json` as above, commit on a branch, merge, then run
`scripts/update-guardrails.sh`.
