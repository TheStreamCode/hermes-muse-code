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

Hermes loads the provider from `$HERMES_HOME/plugins/muse-code-subscription`. Restart long-lived
Hermes processes after installing or after re-running the login script.

## Quick start

1. Start the Meta device login:

   ```powershell
   hermes auth add muse-code
   ```

2. Approve the code in the browser. If the browser does not open, use the printed URL and code.
3. Start a chat with the subscription provider, or pick it from the model picker. Sign in before
   opening the picker: Hermes otherwise treats this provider as an API-key provider.

   ```powershell
   hermes chat -m muse-spark-1.3 --provider muse-code
   hermes model
   ```

The standalone login script remains available for existing installations. The login mints an
account-bound inference key and caches it locally. The cache stores
exactly `apiKey`, `accountId`, and `email`; the OAuth access token from the login flow is kept in
memory only and never written to disk. The key is never printed. A 401 may indicate a revoked key;
the mint endpoint is rate-limited, so the plugin never re-mints on its own.

## Features

- Registers a `muse-code` provider over the Responses API, the same wire as the bundled Meta
  provider, so prompt caching applies. Inference goes through a local loopback wire adapter that
  forwards to `https://api.meta.ai/v1`.
- Runs the wire adapter (`muse_adapter.py`, `127.0.0.1` only, first free port of 43219-43222):
  it aliases only the client tool names that collide with Muse Code's builtin tool names (the full
  pinned set is in [ADAPTER_CONTRACT.md](ADAPTER_CONTRACT.md)), restores them in streamed and JSON
  responses, and normalizes the Meta Responses parameters (`tool_choice`, reasoning effort,
  output-token floor, encrypted-reasoning include). If Meta still refuses the request, the adapter retries once
  with every tool aliased. `MUSE_CODE_ADAPTER=off` keeps the proxy but disables the reshaping.
- Owns the Meta device-code login (RFC 8628); authentication is an explicit, user-driven step, and
  no third-party CLI is required or invoked.
- Discovers models live from your account catalog: every returned model id is served except the
  endpoint families verified as non-chat (`sam-*`, `muse-image-*`, `muse-voice-*`), so newly
  published models appear with no plugin update.
- Exports the cached key as `MUSE_CODE_SUB_TOKEN` in the process environment and refreshes it when
  the cache changes; an explicit value always wins.
- Keeps the credential cache owner-only and never persists the OAuth access token.
- Runs stdlib-only: no runtime dependencies beyond the Hermes host.

## Wire compatibility layer

Meta's Responses endpoint accepts the Muse Code subscription credential only for requests whose
declared flat tool names differ from Muse Code's builtin names. The plugin therefore starts a
loopback reverse proxy (`muse_adapter.py`) on `127.0.0.1` when it loads. It renames exactly the
colliding tool declarations to `client_<name>` (with `_2`, `_3`, ... when that alias is already
taken), restores the original names in streamed and JSON responses, repairs empty function-call
arguments from the streamed deltas, and normalizes four Responses parameters Meta requires
(`tool_choice` to `auto`, `reasoning_effort` to `reasoning.effort`, an output-token floor of 16, and
`reasoning.encrypted_content` in `include`). Other paths, fields and headers are relayed unchanged.

This is a compatibility layer for the Meta Responses wire format, not an authentication change: the
`Authorization` header is forwarded untouched and no credential, token or signature is created.
`ADAPTER_CONTRACT.md` pins the rules; `MUSE_CODE_ADAPTER=off` keeps the loopback proxy but disables
the reshaping. The adapter shuts down with the process.

## Requirements

- Hermes Agent `>= 0.22.0` with model-provider plugin discovery.
- Python 3.11 or newer for the one-time login script; the script uses the standard library only.
- An active Muse Code subscription on the Meta account used at login.
- CI runs the unit suite on Python 3.11 and the local plugin validator.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `MUSE_CODE_SUB_TOKEN` | Explicit subscription key. Wins over the cache; set it to pin a key manually. | from the login cache |
| `MUSE_CODE_SUB_CREDENTIALS` | Overrides the path of the credential cache. | `$HERMES_HOME/muse-code-sub.json` |
| `MUSE_CODE_ADAPTER` | `off` keeps the local proxy but disables the tool-name reshaping and parameter normalization. | unset |

Select the provider as `muse-code` in `hermes model`, or pass `--provider muse-code` to
`hermes chat`. The provider base URL is the loopback adapter's `/v1` origin
(`http://127.0.0.1:<port>/v1`), which forwards to `https://api.meta.ai/v1`; there is no
user-facing endpoint override (`META_BASE_URL` is ignored). If no loopback port can be bound, the
provider falls back to the direct `https://api.meta.ai/v1` endpoint and logs a warning.

## Privacy and security

The subscription key lives owner-only in the local credential cache and in process memory; it is
never logged and never written to a configuration file. The plugin talks only to `auth.meta.com`
for the device login and to `api.meta.ai` for key minting and inference. Inference requests pass
through the local wire adapter, which listens on `127.0.0.1` only, forwards the `Authorization`
header unchanged, and never reads, stores or logs credentials, request bodies or response bodies;
its optional debug line carries only the method, the path, the upstream status and the count of
adapted tool names. The cache stores `apiKey`, `accountId`, and `email`; nothing else is read back.

To remove access, delete `$HERMES_HOME/muse-code-sub.json` (or the path in
`MUSE_CODE_SUB_CREDENTIALS`) and restart Hermes. Run `muse logout` if you also want to clear the
Muse Code CLI session. See [SECURITY.md](SECURITY.md) for vulnerability reporting,
[AUTH_CONTRACT.md](AUTH_CONTRACT.md) for the pinned login contract, and
[ADAPTER_CONTRACT.md](ADAPTER_CONTRACT.md) for the inference wire rules.

## Troubleshooting

### The provider shows as unconfigured

No completed login was found. Run `hermes auth add muse-code`; a missing or empty cache means no
login has completed yet. Confirm that the login used the same `HERMES_HOME` as the Hermes process.

### 401 from the API

The key may have been revoked or rotated. Run `hermes auth add muse-code` once. Do not repeat the
login in a loop: the mint endpoint is rate-limited.

### Meta rejects subscription requests from this client

A 403 mentioning `client_attestation_denied` or that Muse Code subscriptions cover the Muse Code
client only means Meta applied its client restriction to the request. The plugin's loopback wire
adapter exists for exactly this case: it aliases the client tool names that collide with Muse
Code's builtin names, so the refusal should no longer appear for them. If it still does, one of
these is true:

- the adapter could not bind a loopback port, the plugin log names the failure, and the provider
  fell back to the direct endpoint;
- `MUSE_CODE_ADAPTER=off` is set, or an older plugin version is still loaded;
- Meta changed the gate, for example to a real per-client signature. Nothing in this plugin can
  forge a signature, so in that case the subscription cannot be used from Hermes.

The refusal is a client restriction, not evidence of an expired key: signing in again does not
remove it. Use Muse Code for the subscription, or a separate pay-as-you-go Meta Model API key for
other clients, subject to Meta's terms.

### The gateway keeps using the old key

The cache is read once per process. `hermes chat` always sees a fresh login, but long-lived
gateway workers must be restarted after re-running the login script.

### Requests fail with 402 billing_not_configured

An explicit `MUSE_CODE_SUB_TOKEN` wins over the key minted by the subscription login, by design. If
it is set in the process environment or in `$HERMES_HOME/.env` to an old Meta API key (for example
a pay-as-you-go key), Meta answers `402 billing_not_configured` for that key and the subscription
key is never used. Remove the line from `.env` (or from the shell profile) so the plugin exports
the minted key, remove any pooled credential created from it with `hermes auth remove muse-code
<id>`, then restart the gateway with `hermes gateway restart --yes`. `hermes auth status muse-code`
shows the resolved state.

### The provider still calls api.meta.ai directly

A `base_url` under `model:` in `$HERMES_HOME/config.yaml` overrides the provider profile, so the
loopback adapter is bypassed and the client restriction returns. The `hermes model` provider picker
writes that field. Remove the `model.base_url` line and restart the gateway; the plugin's profile
then supplies the adapter URL. Keep `model.provider: muse-code` and the default model.

## Development

Requirements: Python 3.11 or newer and the Hermes checkout used by CI.

```bash
git clone https://github.com/TheStreamCode/hermes-muse-code.git
cd hermes-muse-code
python -m pytest -q
python .github/scripts/validate-plugin.py --path .
ruff check .
```

The unit suite is hermetic: Hermes imports are stubbed, network calls are mocked, and no Meta
account is needed. CI runs the unit suite, the local plugin validator, and `ruff`. The plugin
stays stdlib-only by design; do not add third-party runtime dependencies.

## Support

Usage questions and workflows belong in GitHub Discussions; reproducible defects belong in GitHub
Issues using the bug report form. See [SUPPORT.md](SUPPORT.md). Problems that also reproduce
without this provider belong to the
[Hermes Agent project](https://github.com/NousResearch/hermes-agent/issues). The plugin cannot
obtain, restore, or protect a Meta account, subscription, or credential.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests must pass the required CI checks.

## License

MIT — see [`LICENSE`](LICENSE). The software is provided "as is"; see
[DISCLAIMER.md](DISCLAIMER.md) for the risk notice and liability terms. Flow parameters are
compatible with the published behavior of oh-my-pi (MIT); see [NOTICE](NOTICE).

---

Maintained by [Michael Gasperini (Mikesoft)](https://mikesoft.it).
If this project is useful to you, consider
[sponsoring its maintenance](https://github.com/sponsors/TheStreamCode).
