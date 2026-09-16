# hermes-muse-code

[![ci](https://github.com/TheStreamCode/hermes-muse-code/actions/workflows/ci.yml/badge.svg)](https://github.com/TheStreamCode/hermes-muse-code/actions/workflows/ci.yml)

Use Meta **Muse Spark** in [Hermes Agent](https://github.com/NousResearch/hermes-agent)
billed to your **Muse Code monthly subscription** — no pay-as-you-go API key required.

Hermes' built-in Meta provider only accepts explicit API keys, so subscribers
end up metered twice: once for the subscription, once per token. This plugin
adds a `muse-code` provider that reuses your own Meta login, routing the
same models through the subscription you already pay for.

## Requirements

- Hermes Agent `>= 0.21.3`
- Python 3.11+ (for the one-time login script; stdlib only, no dependencies)

## Install

```powershell
hermes plugins install TheStreamCode/hermes-muse-code --enable
```

## Login (once)

Run the login script, open the printed URL, enter the printed code on the
Meta page, and wait for the poll to complete:

```powershell
python "$env:HERMES_HOME\plugins\muse-code-subscription\muse_code_login.py"
```

This mints a stable, account-bound inference key and caches it locally
(`$HERMES_HOME/muse-code-sub.json`, owner-only permissions where supported).
The key is never printed. Re-run only if access is revoked (401) — the mint
endpoint is aggressively rate-limited, so the plugin never re-mints on its own.

## Usage

```powershell
hermes chat -m muse-spark-1.3 --provider muse-code
```

Or pick provider `muse-code` from `hermes model`. Serves the Muse Spark
family (`muse-spark-1.1` / `1.2` / `1.3`, including `-contributor` tiers).

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `MUSE_CODE_SUB_TOKEN` | Explicit subscription key. Wins over the cache; set it to pin a key manually. | _(from login cache)_ |
| `MUSE_CODE_SUB_CREDENTIALS` | Override path of the credential cache. | `$HERMES_HOME/muse-code-sub.json` |

## How it works

The login script performs a Meta device-code exchange (RFC 8628) and mints
the subscription key via the Model API; the provider reads the cache on
every fresh process and exposes it as `MUSE_CODE_SUB_TOKEN` for a standard
`api_key` provider against `https://api.meta.ai/v1` over the Responses API —
the same wire as the bundled Meta provider, so prompt caching applies. The
key lives only in process memory and is never logged. A standalone login
flow inside the provider is deliberately out of scope: authentication is an
explicit, user-driven step.

## Troubleshooting

- **Provider shows as unconfigured** — run the login script above; a missing
  or empty cache means no login has completed yet.
- **401 from the API** — access was revoked or rotated: re-run the login
  script once.
- **Long-lived gateway workers** — the cache is read once per process:
  `hermes chat` always sees a fresh login, but restart gateway workers after
  re-running the login script.

## Contributing

```powershell
python -m pytest -q
```

Flow parameters are compatible with the published behavior of oh-my-pi (MIT);
see [NOTICE](NOTICE). No third-party CLI is required or invoked.

## License

MIT — see [LICENSE](LICENSE).
