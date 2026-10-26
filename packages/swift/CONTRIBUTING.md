# Contributing to the Swift port

See the [repository's CONTRIBUTING.md](../../CONTRIBUTING.md) first. Its
coverage rules apply here.

## Dependencies

The package depends on Foundation alone, so that it builds on every Apple
platform and on Linux. On Linux, `URLSession` comes from
`FoundationNetworking`.

## Checks

The same checks as CI, from this folder:

    swift format lint --recursive --strict Package.swift Sources Tests
    swift build
    swift test

`swift format format --recursive --in-place Package.swift Sources Tests` fixes
what the lint finds. Its settings are in `.swift-format`.

## HTTP tests

The test server in `Tests/TestServer` is built with the package. The
language-agnostic tests run `.build/debug/TestServer`, so build first:

    swift build
    cd ../../tests
    uv run pytest --package swift

The test server serves `AsyncClient` with `--client async`, and `Client` with
`--client sync`.

## Coverage

The test server runs with coverage during the HTTP tests. The unit tests in
`Tests/UniLLMClientTests` run with plain `swift test`, without coverage. To see
the coverage locally:

    swift build --enable-code-coverage
    rm -rf .build/coverage
    cd ../../tests
    LLVM_PROFILE_FILE="$PWD/../packages/swift/.build/coverage/%p.profraw" uv run pytest --package swift
    cd ../packages/swift
    Scripts/coverage.sh

The script prints a report and writes `coverage.lcov`.

## Platforms

`URLSession` on Linux cuts a request's `timeoutInterval` to whole seconds, so
`URLSessionTransport` keeps the timeout of a call itself, and gives
`URLSession` a longer one as a backstop.

CI runs the HTTP tests on Linux. On macOS it builds the package and runs the
unit tests.
