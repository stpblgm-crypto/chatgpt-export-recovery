#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=../src/browsers/firefox.sh
source "$REPO_ROOT/src/browsers/firefox.sh"

TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/chatgpt-secret-test.XXXXXX")"
trap 'rm -rf -- "$TEST_DIR"' EXIT

database="$TEST_DIR/cookies.sqlite"
work_dir="$TEST_DIR/chatgpt-export-recovery.fixture"
jar="$work_dir/cookies.txt"
meta="$work_dir/cookie-meta"
mkdir -m 700 "$work_dir"

TEST_DATABASE="$database" python3 <<'PY'
import os
import sqlite3
import time

connection = sqlite3.connect(os.environ["TEST_DATABASE"])
connection.execute(
    """
    CREATE TABLE moz_cookies (
        host TEXT,
        path TEXT,
        name TEXT,
        value TEXT,
        expiry INTEGER,
        isSecure INTEGER,
        isHttpOnly INTEGER,
        lastAccessed INTEGER,
        originAttributes TEXT
    )
    """
)
future = int(time.time()) + 3600
connection.executemany(
    "INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    [
        (
            ".chatgpt.com",
            "/",
            "session-test",
            "COOKIE_VALUE_MUST_NEVER_APPEAR_IN_DIAGNOSTICS",
            future,
            1,
            1,
            100,
            "",
        ),
        (
            ".example.test",
            "/",
            "irrelevant",
            "UNRELATED_COOKIE_MUST_NOT_BE_EXTRACTED",
            future,
            1,
            0,
            200,
            "",
        ),
    ],
)
connection.commit()
connection.close()
PY

diagnostics="$(
    CHATGPT_RECOVERY_FIREFOX_COOKIE_DB="$database" \
        firefox_build_temporary_cookie_jar "$jar" "$meta" "$work_dir"
)"

case "$diagnostics" in
    *COOKIE_VALUE_MUST_NEVER_APPEAR_IN_DIAGNOSTICS*|*UNRELATED_COOKIE_MUST_NOT_BE_EXTRACTED*)
        printf 'FAIL: a cookie value appeared in provider diagnostics.\n' >&2
        exit 1
        ;;
esac

if ! rg -q 'COOKIE_VALUE_MUST_NEVER_APPEAR_IN_DIAGNOSTICS' "$jar"; then
    printf 'FAIL: synthetic ChatGPT cookie was not extracted.\n' >&2
    exit 1
fi
if rg -q 'UNRELATED_COOKIE_MUST_NOT_BE_EXTRACTED' "$jar"; then
    printf 'FAIL: an unrelated-domain cookie was extracted.\n' >&2
    exit 1
fi
if [ "$(stat -c '%a' "$jar")" != '600' ] || [ "$(stat -c '%a' "$meta")" != '600' ]; then
    printf 'FAIL: temporary cookie material is not mode 0600.\n' >&2
    exit 1
fi

if rg -n 'https://chatgpt\.com/backend-api/|[?&]sig=' \
    "$REPO_ROOT/src" "$REPO_ROOT/legacy" "$REPO_ROOT/docs" \
    "$REPO_ROOT/README.md" "$REPO_ROOT/SECURITY.md" 2>/dev/null; then
    printf 'FAIL: a concrete signed-URL shape appears in repository content.\n' >&2
    exit 1
fi

printf 'PASS: diagnostics withheld values, unrelated cookies were excluded, and temp files are 0600.\n'
