#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=../src/lib/download.sh
source "$REPO_ROOT/src/lib/download.sh"

TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/chatgpt-range-test.XXXXXX")"
trap 'rm -rf -- "$TEST_DIR"' EXIT

passes=0

expect_pass() {
    local label="$1"
    shift
    if ! "$@"; then
        printf 'FAIL: expected PASS: %s\n' "$label" >&2
        exit 1
    fi
    passes=$((passes + 1))
}

expect_fail() {
    local label="$1"
    shift
    if "$@" 2>/dev/null; then
        printf 'FAIL: expected rejection: %s\n' "$label" >&2
        exit 1
    fi
    passes=$((passes + 1))
}

truncate -s 10 "$TEST_DIR/exact.body"
truncate -s 9 "$TEST_DIR/short.body"
truncate -s 11 "$TEST_DIR/large.body"

expect_pass \
    '206 with an exact range and body' \
    validate_range_response 206 'bytes 0-9/100' 0 9 "$TEST_DIR/exact.body" ''

[ "$RANGE_REMOTE_START" -eq 0 ]
[ "$RANGE_REMOTE_END" -eq 9 ]
[ "$RANGE_REMOTE_TOTAL" -eq 100 ]
[ "$RANGE_BODY_BYTES" -eq 10 ]

expect_fail \
    'HTTP 200 instead of 206' \
    validate_range_response 200 'bytes 0-9/100' 0 9 "$TEST_DIR/exact.body" ''

expect_fail \
    'wrong range start' \
    validate_range_response 206 'bytes 1-10/100' 0 10 "$TEST_DIR/exact.body" ''

expect_fail \
    'short response body' \
    validate_range_response 206 'bytes 0-9/100' 0 9 "$TEST_DIR/short.body" ''

expect_fail \
    'body larger than Content-Range' \
    validate_range_response 206 'bytes 0-9/100' 0 9 "$TEST_DIR/large.body" ''

expect_fail \
    'remote total changed' \
    validate_range_response 206 'bytes 0-9/101' 0 9 "$TEST_DIR/exact.body" 100

expect_fail \
    'server range exceeds requested range' \
    validate_range_response 206 'bytes 0-9/100' 0 8 "$TEST_DIR/exact.body" ''

printf 'PASS: %s Range validation cases behaved as expected.\n' "$passes"
