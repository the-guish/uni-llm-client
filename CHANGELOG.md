# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial Python project structure.
- Anthropic and OpenRouter support
- The reasoning's text from Anthropic's adaptive thinking, which the newer
  models leave out unless asked (EFFORT-7)
- Retries of failed model calls (`max_retries`), and `TransportError` for connection failures and timeouts
- Options for the waits before a retry: `initial_retry_delay`, `max_retry_delay`
  and `max_retry_after`
- Providers of your own: a gateway or another deployment of a known API,
  under a name of your own, with its own key and base URL; and a `timeout`
  per call.
- A JSON form of messages and responses to store conversations in: `to_dict` and `from_dict`
- The Swift port, `UniLLMClient`: `AsyncClient` and the blocking `Client`, with
  Anthropic and OpenRouter, streaming, retries, providers of your own and the
  JSON form of messages (`json`, `init(json:)` and `Codable`)

### Changed


### Fixed


[Unreleased]: https://github.com/open-shipyard/uni-llm-client/commits/main
