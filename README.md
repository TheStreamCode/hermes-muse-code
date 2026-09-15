# hermes-muse-code

[![ci](https://github.com/TheStreamCode/hermes-muse-code/actions/workflows/ci.yml/badge.svg)](https://github.com/TheStreamCode/hermes-muse-code/actions/workflows/ci.yml)

Use Meta **Muse Spark** in [Hermes Agent](https://github.com/NousResearch/hermes-agent)
billed to your **Muse Code monthly subscription** — no pay-as-you-go API key required.

Hermes' built-in Meta provider only accepts explicit API keys, so subscribers
end up metered twice: once for the subscription, once per token. This plugin
adds a `muse-code` provider that reuses the local `omp` login, routing the
same models through the subscription you already pay for.

## Requirements

- Hermes Agent `>= 0.21.3`
- The `omp` CLI (Oh My Pi) with an active Muse Code login

One-time login (browser OAuth, done once in `omp`):

```powershell
omp auth-broker login muse-code
omp usage   # should list your Muse Code account
```

## Install

```powershell
hermes plugins install TheStreamCode/hermes-muse-code --enable
```

## Usage

```powershell
hermes chat -m muse-spark-1.3 --provider muse-code
```

Or pick provider `muse-code` from `hermes model`. Serves the Muse Spark
family (`muse-spark-1.1` / `1.2` / `1.3`, including `-contributor` tiers).

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `MUSE_CODE_SUB_TOKEN` | Explicit subscription key. Wins over the auto-fetch; set it to pin a key manually. | _(resolved from `omp`)_ |
| `HERMES_MUSE_CODE_SUB_AUTO` | Set to `0` to disable the automatic `omp` lookup. | `1` |

## How it works

On load the plugin runs `omp token muse-code --raw` (`omp` owns OAuth and
refresh), takes the subscription-bound `apiKey` from the returned credential,
and exposes it as `MUSE_CODE_SUB_TOKEN` for a standard `api_key` provider
against `https://api.meta.ai/v1` over the Responses API — the same wire as
the bundled Meta provider, so prompt caching applies. The key lives only in
process memory and is never logged. Every fresh `hermes` process resolves a
fresh key; explicit configuration always takes precedence. A standalone login
flow inside the plugin is deliberately out of scope: the OAuth client belongs
to the official apps, so reusing their login is the only stable path.

## Troubleshooting

- **Provider shows as unconfigured** — `omp token muse-code --raw` must print
  a JSON blob. Re-login: `omp auth-broker login muse-code`.
- **401 from the API mid-session** — force an `omp` refresh
  (`omp token muse-code --force-refresh`) and restart the session.
- **Long-lived gateway workers** — the key is resolved once per process:
  `hermes chat` always gets a fresh one, but restart gateway workers after
  an `omp` credential rotation.
- **No `omp` on PATH** — the plugin silently skips; install `omp` first.

## Contributing

```powershell
python -m pytest -q
```

## License

MIT — see [LICENSE](LICENSE).
