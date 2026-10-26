#!/usr/bin/env bash
# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

# Reports the coverage of the test servers after the language-agnostic tests,
# and writes it to coverage.lcov. Run from packages/swift, once the tests ran
# a build made with `swift build --enable-code-coverage`, with
# LLVM_PROFILE_FILE set to .build/coverage/%p.profraw.
set -euo pipefail

# The LLVM tools that came with the Swift toolchain.
tools="$(dirname "$(readlink -f "$(command -v swift)")")"
profile=.build/coverage/merged.profdata

"$tools/llvm-profdata" merge -sparse .build/coverage/*.profraw -o "$profile"
"$tools/llvm-cov" report .build/debug/TestServer -instr-profile="$profile" Sources
"$tools/llvm-cov" export .build/debug/TestServer -instr-profile="$profile" -format=lcov Sources > coverage.lcov
