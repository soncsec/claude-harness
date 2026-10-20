# Architecture

<Two or three sentences: what the system is and how it is shaped.>

## Components

```text
<A small diagram, for example:
  browser --HTTPS--> api (FastAPI) --> SQLite file>
```

| Component | What it does | Built with |
| --- | --- | --- |
| <api> | <...> | <...> |

## Data

<What is stored, where, for how long, and how sensitive it is.>

## Interfaces

<The HTTP endpoints, commands or events that people and other systems use.>

## Security boundaries

<Where untrusted input enters, where identities are checked, and where secrets
live. The details are in the [threat model](threat-model.md).>

## Environments

<What runs locally, in CI, in staging and in production. Agents never deploy:
CI does, and production waits for the operator's approval.>

## Testing

Unit tests for the logic, tests through the public interface (HTTP or
command line), and the checks CI runs on every pull request (`make check`).
<Anything special, such as a test database.>

## Decisions

Each significant choice has an ADR in [docs/adr/](adr/), with the alternatives
considered.
