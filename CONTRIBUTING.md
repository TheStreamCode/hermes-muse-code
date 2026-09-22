# Contributing to hermes-muse-code

Thanks for your interest in improving this project! Bug reports, fixes, docs,
and tests are all welcome. This plugin stays stdlib-only by design — do not
add third-party runtime dependencies.

## Validation

Run the unit suite before opening a pull request:

```bash
python -m pytest -q
```

CI also runs the upstream `plugin-validate` step in non-blocking mode (the
upstream pip guard rejects it; the failure reason is documented in `ci.yml`).

## Pull requests

- Keep changes focused; one concern per PR.
- Add or update tests for behavior changes.
- Never commit credentials, subscription keys, or account data.
- Never log the cached subscription key in code or fixtures.
