#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
checked=0

while IFS= read -r -d '' script; do
    bash -n "$script"
    checked=$((checked + 1))
done < <(
    find "$REPO_ROOT" -type f \
        \( -name '*.sh' -o -path "$REPO_ROOT/src/chatgpt-export-recover" \) \
        -print0
)

if [ "$checked" -lt 7 ]; then
    printf 'FAIL: expected at least 7 shell scripts, checked %s\n' "$checked" >&2
    exit 1
fi

printf 'PASS: bash -n accepted %s shell scripts.\n' "$checked"
