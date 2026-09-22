# Contributing to hermes-muse-code

Thanks for your interest in improving this project! Bug reports, fixes, docs,
and tests are all welcome. This plugin stays stdlib-only by design — do not
add third-party runtime dependencies.

## Validation

Run the unit suite before opening a pull request:

```bash
python -m pytest -q
```

CI also runs the local plugin validator (`.github/scripts/validate-plugin.py`),
which mirrors the upstream `plugin-validate` admission checks without installing
hermes-agent (the upstream action's pip install is rejected by hermes-agent's own
setup.py guard, and the action has no last-good version to pin; see `ci.yml`).

## Pull requests

- Keep changes focused; one concern per PR.
- Add or update tests for behavior changes.
- Never commit credentials, subscription keys, or account data.
- Never log the cached subscription key in code or fixtures.
