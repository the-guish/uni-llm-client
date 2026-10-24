# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial Python project structure.
- Anthropic and OpenRouter support
- Retries of failed model calls (`max_retries`), and `TransportError` for connection failures and timeouts
- Providers of your own: a gateway or another deployment of a known API,
  under a name of your own, with its own key and base URL; and a `timeout`
  per call. Specified in `docs/specs/providers.md`.

### Changed


### Fixed


[Unreleased]: https://github.com/open-shipyard/uni-llm-client/commits/main
