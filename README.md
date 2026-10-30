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

- Registers a `muse-code` provider against `https://api.meta.ai/v1` over the Responses API, the
  same wire as the bundled Meta provider, so prompt caching applies.
- Owns the Meta device-code login (RFC 8628); authentication is an explicit, user-driven step, and
  no third-party CLI is required or invoked.
- Discovers models live from your account catalog: every returned model id is served except the
  endpoint families verified as non-chat (`sam-*`, `muse-image-*`, `muse-voice-*`), so newly
  published models appear with no plugin update.
- Exports the cached key as `MUSE_CODE_SUB_TOKEN` in the process environment and refreshes it when
  the cache changes; an explicit value always wins.
- Keeps the credential cache owner-only and never persists the OAuth access token.
- Runs stdlib-only: no runtime dependencies beyond the Hermes host.

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

Select the provider as `muse-code` in `hermes model`, or pass `--provider muse-code` to
`hermes chat`. The base URL is fixed to the Meta Model API; there is no endpoint override.

## Privacy and security

The subscription key lives owner-only in the local credential cache and in process memory; it is
never logged and never written to a configuration file. The plugin talks only to `auth.meta.com`
for the device login and to `api.meta.ai` for key minting and inference. The cache stores
`apiKey`, `accountId`, and `email`; nothing else is read back.

To remove access, delete `$HERMES_HOME/muse-code-sub.json` (or the path in
`MUSE_CODE_SUB_CREDENTIALS`) and restart Hermes. Run `muse logout` if you also want to clear the
Muse Code CLI session. See [SECURITY.md](SECURITY.md) for vulnerability reporting and
[AUTH_CONTRACT.md](AUTH_CONTRACT.md) for the pinned login contract.

## Troubleshooting

### The provider shows as unconfigured

No completed login was found. Run `hermes auth add muse-code`; a missing or empty cache means no
login has completed yet. Confirm that the login used the same `HERMES_HOME` as the Hermes process.

### 401 from the API

The key may have been revoked or rotated. Run `hermes auth add muse-code` once. Do not repeat the
login in a loop: the mint endpoint is rate-limited.

### Meta rejects subscription requests from this client

Meta may return a 403 mentioning `client_attestation_denied` or that Muse Code subscriptions cover
the Muse Code client only. This is a client restriction, not evidence of an expired key. Signing in
again does not remove the restriction. Use Muse Code for the subscription, or a separate
pay-as-you-go Meta Model API key for other clients, subject to Meta's terms.

### The gateway keeps using the old key

The cache is read once per process. `hermes chat` always sees a fresh login, but long-lived
gateway workers must be restarted after re-running the login script.

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
