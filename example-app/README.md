# Notes API (claude-harness example app)

A small FastAPI service that shows what a project built with claude-harness
looks like: users sign up, log in, and manage their own notes.

It is deliberately small, but it covers the security basics the harness cares
about, each with tests that must never regress:

| Concern | How it is handled | Tests |
| --- | --- | --- |
| Password storage | scrypt at the OWASP minimum cost, random salt per user | `test_security.py` |
| Login tokens | Random tokens; only a SHA-256 digest is stored; expiry; logout revokes | `test_api.py` |
| Username enumeration | Wrong password and unknown user give the same answer, in the same time | `test_wrong_password_and_unknown_user_look_the_same` |
| Access control | Every note query is scoped to the logged-in user; others' notes look missing | `test_users_cannot_touch_each_others_notes` |
| SQL injection | Only `?` placeholders, never SQL built from input | `test_sql_injection_is_stored_as_plain_text` |
| Input limits | Length and format checks on every field | `test_note_validation`, `test_sign_up_validation` |
| Browser hardening | `nosniff`, `DENY` framing, `no-store` caching headers | `test_security_headers_are_set` |

## Run it

```sh
make setup   # install exactly what uv.lock says
make test    # run the tests
make run     # http://127.0.0.1:8000, interactive docs at /docs
```

Settings come from environment variables; see [env.example](env.example).

## Dependencies

Runtime: `fastapi`, `uvicorn`. Everything else (database, hashing, tokens) uses
the Python standard library. `uv` ignores package releases younger than 7 days
(`[tool.uv] exclude-newer` in `pyproject.toml`), a cooldown that skips most
malicious releases, which are usually pulled within days.
