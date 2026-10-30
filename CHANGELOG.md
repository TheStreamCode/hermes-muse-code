# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] - 2026-10-30

### Added

- Local loopback wire adapter (`muse_adapter.py`, `ADAPTER_CONTRACT.md` v3) in front of
  `api.meta.ai`: it aliases client tool names that collide with Muse Code's builtin tool names
  (`read_file`, `bash`, `web_search`, ...), restores them in streamed and JSON responses, repairs
  empty function-call arguments from the SSE deltas, and normalizes `tool_choice`, reasoning
  effort, the output-token floor and the encrypted-reasoning include. Subscription inference keeps
  working after Meta's 2026-10-29 client restriction. The collision set covers the 40 Muse
  builtin names (goal, cron, session and patch families included), and a refusal not covered by it
  triggers one retry with every tool aliased.
- `MUSE_CODE_ADAPTER=off` keeps the local proxy but disables the reshaped tool declarations and the
  parameter normalization.

### Documented

- Troubleshooting for two deployment overrides that bypass the adapter: an explicit
  `MUSE_CODE_SUB_TOKEN` in the environment or `$HERMES_HOME/.env` (Meta answers `402
  billing_not_configured` for a stale pay-as-you-go key), and a `base_url` under `model:` in
  `$HERMES_HOME/config.yaml` written by the `hermes model` picker.

### Changed

- The `muse-code` provider base URL is now the adapter's `/v1` origin instead of the direct Meta
  endpoint; repeated imports and plugin registrations reuse the same server. If no loopback port
  can be bound, the provider falls back to `https://api.meta.ai/v1` with a warning (the pre-0.6
  behavior).
- `AUTH_CONTRACT.md` points at `ADAPTER_CONTRACT.md` (v3) for the inference wire contract; the
  pinned login values are unchanged.

### Fixed

- Meta's 403 `client_attestation_denied` no longer appears for requests that declare colliding flat
  tool names while the adapter is running. The provider still classifies the refusal as a policy
  block if Meta changes the gate.

## [0.5.0] - 2026-10-30

### Added

- `hermes auth add muse-code` runs the Meta device login in the browser and stores the credential in
  the existing cache.

### Fixed

- Meta's client restriction (403) is classified as a provider policy block instead of a revoked
  credential, so the host no longer recommends re-authentication for it.
- Login errors no longer echo endpoint response bodies.

### Changed

- The standalone login script remains available; the README documents the browser flow and the
  restriction.

## [0.4.0] - 2026-10-21

### Fixed

- The subscription provider ignored `META_BASE_URL` and could be pointed at another host; the base
  URL is now pinned to the subscription endpoint.
- `MUSE_CODE_SUB_TOKEN` is refreshed from the credential cache without overwriting an explicit
  value set in the environment.
- Importing the plugin no longer exports the subscription key as a side effect.
- Third-party plugins may not import private Hermes core modules: the Meta effort vocabulary and
  the `clamp_effort` contract are now vendored locally (provenance: `agent/reasoning_effort.py` at
  Hermes v0.22.0) with identical behavior. `hermes plugins validate` is clean.

### Changed

- Requires Hermes Agent 0.22.0 or newer.
- Added `AUTH_CONTRACT.md`, ruff configuration and regression tests; repository documentation and
  community files aligned with the shared standard.

## [0.3.0] - 2026-10-14

### Added

- Live model discovery: every model id returned by the account catalog is served except the
  endpoint families verified as non-chat (`sam-*`, `muse-image-*`, `muse-voice-*`). Newly
  published chat models appear with no plugin update.

## [0.2.2] - 2026-09-27

### Fixed

- Live discovery admits only `muse-spark-*` model ids; the endpoint also serves non-chat families
  such as `sam-*`.

## [0.2.1] - 2026-09-27

### Security

- The credential cache no longer persists the Meta OAuth access token; it stores `apiKey`,
  `accountId`, and `email` only. Re-run the login script to refresh caches written by earlier
  versions.

### Fixed

- Device-flow polling tolerates the HTTP-error responses that Meta returns for a pending
  authorization.
- The signup and subscription URLs match the documented Meta pages, and the `BASE_URL` override is
  documented.

### Changed

- Release automation, Dependabot configuration, and community files added.
- The auxiliary model choice is documented.

## [0.2.0] - 2026-09-22

### Added

- Muse Spark model provider for Hermes Agent billed to the Muse Code monthly subscription through
  a one-time Meta device-code login; the credential cache is owner-only and stores no OAuth access
  token.

### Changed

- The plugin owns the device-code login and no longer depends on an external CLI.

## [0.1.0] - 2026-09-15

### Added

- Initial Muse Spark provider for Hermes Agent billed to the Muse Code subscription.
- Probe-safe `register()` entry point for plugin discovery, with catalog handling and
  credential-secrecy tests.

[0.6.0]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.6.0
[0.5.0]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.5.0
[0.4.0]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.4.0
[0.3.0]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.3.0
[0.2.2]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.2.2
[0.2.1]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.2.1
[0.2.0]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.2.0
[0.1.0]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.1.0
