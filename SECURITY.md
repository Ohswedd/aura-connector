# Security Policy

## Supported versions

The project is at an early stage. Security fixes are applied to the latest released
version on the `main` branch.

| Version | Supported |
|---|---|
| 0.1.x | yes |
| < 0.1 | no |

## Reporting a vulnerability

Please report suspected vulnerabilities privately. Do not open a public issue for a
security problem, because a public issue discloses the vulnerability before a fix is
available.

Use GitHub's private vulnerability reporting (the "Report a vulnerability" button under
the repository's Security tab) to open a confidential advisory. Include a description, a
minimal reproduction if possible, and the affected version.

You can expect an acknowledgement within a few business days. Once a fix is available we
will coordinate a disclosure timeline with you.

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
