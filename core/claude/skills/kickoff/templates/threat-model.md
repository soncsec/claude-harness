# Threat model

Last reviewed: <YYYY-MM-DD>. Review it whenever a change touches sign-in,
sensitive data, untrusted input, secrets or a new dependency (issues labeled
`security`).

## What we protect

| Asset | Why it matters | Sensitivity |
| --- | --- | --- |
| <User accounts> | <...> | <Personal> |

## Entry points

<Where input comes from: the public HTTP API, uploaded files, the command line,
other services, dependencies.>

## Trust boundaries

<Where data crosses from less trusted to more trusted: internet to API, API to
database, service to service.>

## Threats

Categories (STRIDE): Spoofing, Tampering, Repudiation, Information disclosure,
Denial of service, Elevation of privilege.

| # | Threat | Category | Mitigation | Status |
| --- | --- | --- | --- | --- |
| T1 | <A user reads another user's data> | Information disclosure | <Every query is scoped to the signed-in user, and a test proves it> | <Mitigated> |

Status is one of: **Mitigated** (in place and tested), **Planned #<issue>** (an
issue labeled `security` tracks it), or **Accepted** (the operator chose to live
with it; say why).

## Covered by the harness

These need no rows above:

- Secrets are kept out of commits (gitleaks in CI), and agents cannot read them.
- Dependencies are locked, scanned for known vulnerabilities, and new releases
  are held back for 7 days.
- Code is scanned by Semgrep on every pull request; error-level findings block
  the merge.
- Agents cannot change their own guardrails, merge pull requests or deploy.
