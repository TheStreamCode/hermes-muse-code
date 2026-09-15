# hermes-muse-code

[![ci](https://github.com/TheStreamCode/hermes-muse-code/actions/workflows/ci.yml/badge.svg)](https://github.com/TheStreamCode/hermes-muse-code/actions/workflows/ci.yml)

Use Meta **Muse Spark** in [Hermes Agent](https://github.com/NousResearch/hermes-agent)
billed to your **Muse Code monthly subscription** — no pay-as-you-go API key.

Hermes' built-in `meta-ai` provider only accepts explicit API keys, so
subscription users burn pay-as-you-go tokens there while the same model bills
to their subscription elsewhere (upstream: Hermes issue
[#102535](https://github.com/NousResearch/hermes-agent/issues/102535), PR
[#102536](https://github.com/NousResearch/hermes-agent/pull/102536) — macOS
only). This plugin closes the gap wherever the `omp` CLI is installed by
reusing its Muse Code login.

## Requirements

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

Or pick provider `muse-code` from `hermes model`. Available models are the
Muse Spark family (`muse-spark-1.1/1.2/1.3`, `-contributor` tiers).

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `MUSE_CODE_SUB_TOKEN` | Explicit subscription key. Wins over the auto-fetch; set this to pin a key manually. | _(fetched from `omp`)_ |
| `HERMES_MUSE_CODE_SUB_AUTO` | Set to `0` to disable the automatic `omp` lookup. | `1` |

## How it works

On load the plugin runs `omp token muse-code --raw` (a JSON blob;
`omp` owns OAuth + refresh), extracts the subscription-bound `apiKey`
field, and exposes it as `MUSE_CODE_SUB_TOKEN` for a regular `api_key`
provider against `https://api.meta.ai/v1` over the Responses API
(`codex_responses`, same wire as the bundled `meta-ai` provider, so prompt
caching applies). The key lives only in process memory and is never logged.
Each fresh `hermes` process resolves a fresh key; explicit configuration
always keeps priority. A standalone login flow inside the plugin is
deliberately out of scope: Meta's OAuth client is private to the official
apps, so reusing their login is the only stable path.

## Troubleshooting

- **Provider shows as unconfigured** — `omp token muse-code --raw` must print
  a JSON blob. Re-login: `omp auth-broker login muse-code`.
- **401 from the API mid-session** — force an `omp` refresh
  (`omp token muse-code --force-refresh`) and restart the session.
- **Long-lived gateway workers** — the key is resolved once per process:
  `hermes chat` always gets a fresh one, but restart gateway workers after
  an `omp` credential rotation.

## Contributing

```powershell
python -m pytest -q
```

## License

MIT — see [LICENSE](LICENSE).
