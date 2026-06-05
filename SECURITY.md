# Security Policy

## Supported versions

Aura Connector is at `0.3.0`. Security fixes target the latest released version on
the `main` branch.

| Version | Supported |
|---|---|
| 0.3.x | yes |
| < 0.3 | no |

## Reporting a vulnerability

Please report suspected vulnerabilities privately. Do not open a public issue for a
security problem, because a public issue discloses the vulnerability before a fix is
available.

Use GitHub's private vulnerability reporting (the "Report a vulnerability" button under
the repository's Security tab) to open a confidential advisory. Include a description, a
minimal reproduction if possible, and the affected version.

You can expect an acknowledgement within a few business days. Once a fix is available we
will coordinate a disclosure timeline with you.

## Transport security and authentication

The native AuraDB backend (`auradb://` plaintext, `auradbs://` TLS) supports:

- **TLS** via `TLSConfig`: the server certificate is verified against a trusted CA with
  hostname verification, and optional client certificates enable mutual TLS.
- **Static-token authentication** via `TokenAuth`: the token is presented during the Aura
  Wire Protocol handshake and is never logged; an authentication failure raises
  `AuraAuthenticationError`.

Credentials live in configuration objects that redact them in their `repr`, so logging a
config does not expose a token or key.

## Injection safety

Queries are built through a typed, immutable builder that compiles to an abstract syntax
tree and Query IR; they are never assembled by string concatenation, so a value can never
be interpreted as query structure. Backend adapters bind values as parameters.

## Handling of secrets

The client is designed to keep secrets out of logs and error output:

- Configuration objects redact credentials in their `repr`.
- Errors carry stable codes and non-sensitive context, and never embed credentials.
- Query fingerprints are computed without the bound literal values they summarize.

## Protocol validation

The wire protocol is validated defensively:

- Every frame is parsed against a fixed binary layout with a version check.
- Frame headers and payloads carry checksums that are verified on decode.
- Payload lengths are bounded, and malformed or oversized frames are rejected.
- The client fails closed on protocol errors rather than continuing in an unknown state.
