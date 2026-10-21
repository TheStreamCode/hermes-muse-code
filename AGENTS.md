# Agent instructions

Repository: hermes-muse-code
Maintainer: Michael Gasperini (Mikesoft)

This repository is an unofficial Hermes Agent plugin that serves Muse models billed to a Muse Code
subscription. The unofficial-use framing in `DISCLAIMER.md` and the pinned wire values in
`AUTH_CONTRACT.md` are part of the contract: keep them intact and in sync with the sibling Muse
plugins (`dsh-muse-code`, `openclaw-muse-code`, `opencode-muse-auth`).

## Layout

- `__init__.py` — directory-plugin entry point and provider profile; registers on import.
- `muse_code_login.py` — one-time Meta device-code login and key mint; standard library only.
- `plugin.yaml` — Hermes plugin manifest (name, version, required Hermes version).
- `tests/` — hermetic `pytest` suite; new behavior needs a test.
- `.github/scripts/validate-plugin.py` — local copy of the upstream plugin admission checks.
- `.github/workflows/` — CI; do not weaken checks, pins, or permissions.

## Commands

```bash
python -m pytest -q
python .github/scripts/validate-plugin.py --path .
ruff check .
```

Use Python 3.11 or newer. No third-party runtime dependencies are allowed; tests may use `pytest`.

## Constraints

- Keep changes focused; no drive-by refactors, no new runtime dependencies.
- Public documentation, comments, and commit messages are in English.
- Do not change `LICENSE`, `NOTICE`, `DISCLAIMER.md`, `AUTH_CONTRACT.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, or release metadata unless the task is about them.
- Never log, print, commit, or describe a subscription key, OAuth token, or account data. The cache
  stores `apiKey`, `accountId`, and `email` only; keep it that way.
- Never add instructions that bypass Meta's terms or credential rules.
- Releases are tag-driven; do not create tags, releases, or publish artifacts.
- Run the checks above before proposing a change.
