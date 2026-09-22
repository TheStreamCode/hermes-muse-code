# Security Policy

## Reporting a Vulnerability

Use GitHub's
[private vulnerability reporting](https://github.com/TheStreamCode/hermes-muse-code/security/advisories/new)
for sensitive findings. If that route is unavailable, email
`info@mikesoft.it` with the subject `Hermes Muse Code Security Report`.
Do not disclose API keys, credentials, private prompts, or account data in a
public issue.

For non-sensitive security hardening, open an issue with the affected component,
observed behavior, impact, and a minimal reproduction where practical.

## Supported Version

Security fixes target the latest published release and the current `main` branch.

## Credential Handling

This project must never log, persist, or commit Muse subscription credentials.
The subscription key lives owner-only in the local credential cache and in
process memory; it is never written to logs. Local credentials belong in the
active Hermes profile's ignored environment configuration.
