# hermes-muse-code

Muse Spark in Hermes Agent billed to the Muse Code subscription

[![CI](https://github.com/TheStreamCode/hermes-muse-code/actions/workflows/ci.yml/badge.svg)](https://github.com/TheStreamCode/hermes-muse-code/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/TheStreamCode/hermes-muse-code)](https://github.com/TheStreamCode/hermes-muse-code/releases/latest)

Provider plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that signs in with
a Muse Code subscription. A successful login does not guarantee model access: Meta may reject
subscription requests from clients other than Muse Code.

> **Unofficial and experimental.** This plugin uses a Muse Code subscription credential from
> Hermes Agent. Meta's terms limit that credential to Muse Code, so this use may not be supported
> and Meta may suspend or revoke accounts or credentials. Use it at your own risk; the authors
> cannot recover an account. Read [DISCLAIMER.md](DISCLAIMER.md) before installing.

## Install

```powershell
hermes plugins install TheStreamCode/hermes-muse-code --enable
```

Hermes loads the provider from `$HERMES_HOME/plugins/muse-code-subscription`; restart long-lived
Hermes processes after installing or re-running the login script.

## Quick start

1. Start the Meta device login:

   ```powershell
   hermes auth add muse-code
   ```

2. Approve the code in the browser. If the browser does not open, use the printed URL and code.
3. Sign in before opening the model picker (Hermes otherwise treats this provider as an API-key
   provider), then start a chat:

   ```powershell
   hermes chat -m muse-spark-1.3 --provider muse-code
   hermes model
   ```

The login mints an account-bound inference key and caches it locally (a standalone login script
remains for existing installations). The cache stores exactly `apiKey`, `accountId`, and `email`;
the OAuth access token stays in memory, never on disk, and the key is never printed. A 401 may
mean a revoked key: run the login once more, but do not loop, since the mint endpoint is
rate-limited and the plugin never re-mints on its own.

## Features

- Registers a `muse-code` provider over the Responses API (the bundled Meta provider's wire, so
  prompt caching applies), forwarding inference through a loopback adapter to
  `https://api.meta.ai/v1`.
- Runs the wire adapter (`muse_adapter.py`, `127.0.0.1` only, first free port of 43219-43222):
  it renames colliding client tool declarations to `client_<name>`, restores them in
  responses, and normalizes the Responses parameters Meta requires. If Meta still refuses,
  it retries once with every tool renamed. `MUSE_CODE_ADAPTER=off` keeps the proxy but disables
  the reshaping. Rules are pinned in [ADAPTER_CONTRACT.md](ADAPTER_CONTRACT.md).
- Owns the Meta device-code login (RFC 8628): explicit, user-driven authentication with no
  third-party CLI.
- Serves every model id from your live account catalog except the verified non-chat endpoint
  families (`sam-*`, `muse-image-*`, `muse-voice-*`), so new models appear with no plugin
  update.
- Exports the cached key as `MUSE_CODE_SUB_TOKEN`, refreshed on cache changes; an explicit value
  always wins.
- Runs stdlib-only: no runtime dependencies beyond the Hermes host.

## Requirements

- Hermes Agent `>= 0.22.0` with model-provider plugin discovery.
- Python 3.11 or newer for the stdlib-only one-time login script.
- An active Muse Code subscription on the Meta account used at login.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `MUSE_CODE_SUB_TOKEN` | Explicit subscription key. Wins over the cache; set it to pin a key manually. | from the login cache |
| `MUSE_CODE_SUB_CREDENTIALS` | Overrides the path of the credential cache. | `$HERMES_HOME/muse-code-sub.json` |
| `MUSE_CODE_ADAPTER` | `off` keeps the proxy but disables the reshaping and normalization. | unset |

Select the `muse-code` provider in `hermes model` or via `--provider muse-code`. The
provider base URL is the loopback adapter's `/v1` origin (`http://127.0.0.1:<port>/v1`),
forwarding to `https://api.meta.ai/v1`; there is no endpoint override (`META_BASE_URL` is
ignored). If no loopback port binds, the provider falls back to `https://api.meta.ai/v1`
directly and logs a warning.

## Privacy and security

The subscription key lives owner-only in the local cache and process memory; it is never logged
or written to a configuration file. Network use is limited to `auth.meta.com` (device login) and
`api.meta.ai` (key minting, inference). The local adapter listens on `127.0.0.1` only, forwards
`Authorization` unchanged, and never reads, stores, or logs credentials or bodies; its optional
debug line carries only the method, path, status, and adapted-tool count.

To remove access, delete `$HERMES_HOME/muse-code-sub.json` (or the path in
`MUSE_CODE_SUB_CREDENTIALS`) and restart Hermes. Run `muse logout` to also clear the Muse Code
CLI session. See [SECURITY.md](SECURITY.md) for vulnerability reporting,
[AUTH_CONTRACT.md](AUTH_CONTRACT.md) (login), and [ADAPTER_CONTRACT.md](ADAPTER_CONTRACT.md)
(wire rules).

## Troubleshooting

### The provider shows as unconfigured

No completed login was found: run `hermes auth add muse-code`. A missing or empty cache means no
login completed yet under the same `HERMES_HOME` as the Hermes process.

### Meta rejects requests from this client

A 403 with `client_attestation_denied` means Meta applied its client restriction (Muse Code
subscriptions cover the Muse Code client only). The wire adapter normally renames the colliding
tool names away; if the refusal persists, one of these is true:

- the adapter is out of the path: no loopback port bound (the plugin log names the failure),
  `MUSE_CODE_ADAPTER=off` is set, or an older plugin version is still loaded;
- a `base_url` under `model:` in `$HERMES_HOME/config.yaml` (written by the `hermes model`
  picker) overrides the provider profile and bypasses the adapter. Remove the `model.base_url`
  line, keep `model.provider: muse-code`, and restart the gateway;
- Meta changed the gate to a real per-client signature, which nothing in this plugin can forge;
  the subscription then cannot be used from Hermes.

Signing in again cannot remove a client restriction (the key is not expired). Use Muse Code for
the subscription, or a separate pay-as-you-go Meta Model API key for other clients, subject to
Meta's terms.

### The gateway keeps the old key

The cache is read once per process: `hermes chat` always sees a fresh login, but restart gateway
workers after each login. After `hermes plugins update muse-code-subscription`,
likewise restart: the running gateway keeps the previous plugin version until `hermes gateway
restart --yes`.

### Requests fail with 402 billing_not_configured

An explicit `MUSE_CODE_SUB_TOKEN` wins over the minted key, by design. If the environment
or `$HERMES_HOME/.env` sets it to an old Meta API key (for example a pay-as-you-go key), Meta
answers `402 billing_not_configured` and the subscription key is never used. Remove that line so
the plugin exports the minted key, drop any pooled credential made from it (`hermes auth remove
muse-code <id>`), then run `hermes gateway restart --yes`. `hermes auth status muse-code` shows
the resolved state.

## Development

Requirements: Python 3.11 or newer and the Hermes checkout used by CI.

```bash
git clone https://github.com/TheStreamCode/hermes-muse-code.git
cd hermes-muse-code
python -m pytest -q
python .github/scripts/validate-plugin.py --path .
ruff check .
```

The hermetic unit suite stubs Hermes imports and mocks network calls, so no Meta account is
needed. CI runs the unit suite on Python 3.11, the local plugin validator, and `ruff`. The
plugin stays stdlib-only; do not add third-party runtime dependencies.

## Support

Usage questions and workflows belong in GitHub Discussions; reproducible defects belong in GitHub
Issues (bug report form; see [SUPPORT.md](SUPPORT.md)). Problems that also reproduce without this
provider belong to the
[Hermes Agent project](https://github.com/NousResearch/hermes-agent/issues). The plugin cannot
obtain, restore, or protect a Meta account, subscription, or credential.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests must pass the required CI checks.

## License

MIT — see [`LICENSE`](LICENSE); the software is provided "as is" (see
[DISCLAIMER.md](DISCLAIMER.md) for the risk notice and liability terms). Flow parameters are
compatible with the published behavior of oh-my-pi (MIT); see [NOTICE](NOTICE).

---

Maintained by [Michael Gasperini (Mikesoft)](https://mikesoft.it).
If this project is useful to you, consider
[sponsoring its maintenance](https://github.com/sponsors/TheStreamCode).
