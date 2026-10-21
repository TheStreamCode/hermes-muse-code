# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.3.0]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.3.0
[0.2.2]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.2.2
[0.2.1]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.2.1
[0.2.0]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.2.0
[0.1.0]: https://github.com/TheStreamCode/hermes-muse-code/releases/tag/v0.1.0
