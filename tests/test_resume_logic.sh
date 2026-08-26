#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=../src/lib/download.sh
source "$REPO_ROOT/src/lib/download.sh"

TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/chatgpt-resume-test.XXXXXX")"
trap 'rm -rf -- "$TEST_DIR"' EXIT

part="$TEST_DIR/export.zip.part"
segment="$TEST_DIR/next.segment"
printf 'ABCD' > "$part"
printf 'EFGH' > "$segment"

validate_range_response 206 'bytes 4-7/8' 4 7 "$segment" 8
append_verified_segment "$part" "$segment" 4 8

result="$(command cat -- "$part")"
if [ "$result" != 'ABCDEFGH' ]; then
    printf 'FAIL: resume append produced unexpected content.\n' >&2
    exit 1
fi

size="$(stat -c '%s' "$part")"
if [ "$size" -ne 8 ]; then
    printf 'FAIL: resume checkpoint is %s bytes; expected 8.\n' "$size" >&2
    exit 1
fi

printf 'PASS: existing checkpoint plus one verified segment advanced to 8 bytes.\n'
