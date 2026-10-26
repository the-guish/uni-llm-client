# Contributing to uni-llm-client

## Branching model

`main` is the only long-lived branch and must always be releasable. Direct
pushes to `main` are blocked.

1. Create a short-lived branch from `main` for each change, prefixed with
   `feature/` for new functionality or `fix/` for corrections:

       git switch main && git pull
       git switch -c feature/short-description

2. Open a pull request against `main`.
3. A maintainer merges it once checks pass and review is complete.

Maintainers cut releases by tagging a commit on `main` with a
[Semantic Versioning](https://semver.org/) tag such as `v0.2.0`. Fixes for a
release go through a regular pull request to `main` and ship in the next tag.

## Repository layout

Each port lives in `packages/<language>`. The language-agnostic tests live in
`tests`, and every port must pass them. A port joins them through its test
server, described in [tests/README.md](tests/README.md).

CI checks a package only when it changes. A change to `tests` runs the
language-agnostic tests of every package. A change to the CI workflow runs
everything. The `ci-ok` job passes when every other job passed or was skipped.

Each package has its own SonarQube Cloud project, set in its
`sonar-project.properties`. The root project covers `.github` and `tests`.

## Tests

The HTTP tests in [`tests`](tests/README.md) are the tests of every port. A new
behaviour gets an HTTP case, and every port passes it.

A port's own tests are the exception. They cover only what no HTTP case can
reach, such as an edge of a pure function or the environment the package runs
in. A port's test that adds no line or branch to the HTTP tests' coverage is
removed.

A few HTTP tests wait in real time, such as the tests of the waits before a
retry. They are marked `slow`. A plain run includes them, and so does CI. A
quick local run skips them with `-m "not slow"`:

    cd tests
    uv run pytest -m "not slow"

## Coverage

Coverage comes from the HTTP tests in [`tests`](tests/README.md) alone. Each
port runs its test server under its coverage tool while the HTTP tests drive
it. Unit tests report no coverage, in every port. Each package sends one report
to its SonarQube Cloud project.

The commands for each port are in its own CONTRIBUTING.md, such as
[packages/python/CONTRIBUTING.md](packages/python/CONTRIBUTING.md).

## Development setup

The Python port uses [uv](https://docs.astral.sh/uv/), and so do the
language-agnostic tests. Run the same checks as CI before opening a
pull request, in `packages/python` and in `tests`:

    uv sync
    uv run ruff check
    uv run ruff format --check
    uv run mypy
    uv run pytest

The TypeScript port uses npm. Installs are `npm ci`, never `npm install`:

    cd packages/typescript
    npm ci
    npm run typecheck

The Swift port needs Swift 6, from Xcode or from
[swift.org/install](https://www.swift.org/install/) on Linux. Its HTTP tests
run on the build of its test server:

    cd packages/swift
    swift format lint --recursive --strict Package.swift Sources Tests
    swift build
    swift test
    cd ../../tests
    uv run pytest --package swift

New source files start with the license header, in the language's comment
syntax, such as `//` in Swift:

    # Copyright 2026 The uni-llm-client Authors
    # SPDX-License-Identifier: Apache-2.0

The Python package ships copies of `LICENSE`, `NOTICE` and `AUTHORS`. Edit the
files at the root, then copy them to `packages/python`.

## Developer Certificate of Origin

All contributions must be signed off under the
[Developer Certificate of Origin 1.1](https://developercertificate.org/).
Add `-s` to your commit:

    git commit -s -m "component: short imperative summary"

which appends:

    Signed-off-by: Jane Doe <jane@example.com>

Use your real name and a reachable email address. Sign-off is a statement about
the provenance of your contribution. Pseudonymous sign-offs are not accepted. A
contribution on behalf of an employer needs the employer's authorization.
