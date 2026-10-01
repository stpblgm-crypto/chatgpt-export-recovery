#!/usr/bin/env bash
# Entirely synthetic curl responses. No network or real browser is used.
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$REPO_ROOT/src/lib/download.sh"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/chatgpt-engine-test.XXXXXX")"
trap 'rm -rf -- "$TEST_DIR"' EXIT
python3 - "$TEST_DIR/source.zip" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'w', compression=zipfile.ZIP_STORED) as archive:
    archive.writestr('synthetic.txt', b'offline fixture only' * 10)
PY
source_zip="$TEST_DIR/source.zip"
source_size="$(stat -c %s "$source_zip")"
scenario=valid
calls=0
sleep() { :; }
sync() { printf '%s\n' "sync" >> "$case_dir/sync.calls"; }
df() {
    if [ "$scenario" = lowdisk ]; then
        printf 'Filesystem 1B-blocks Used Available Use%% Mounted on\nsynthetic 1 1 0 100%% /\n'
    else
        command df "$@"
    fi
}
# Implements only this engine's curl contract and writes fixtures to its temp paths.
curl() {
    local header= body= range= config= start end total cap= deadline=
    if [ "${2-}" = --version ]; then printf 'curl 8.14.1 synthetic\n'; return 0; fi
    calls=$((calls + 1))
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --dump-header) header="$2"; shift 2 ;;
            --output) body="$2"; shift 2 ;;
            --range) range="$2"; shift 2 ;;
            --config) config="$2"; shift 2 ;;
            --max-filesize) cap="$2"; shift 2 ;;
            --max-time) deadline="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    [ "$(stat -c %a "$config")" = 600 ] || return 95
    printf '%s\n' "$range" >> "$case_dir/ranges"
    start="${range%-*}"; end="${range#*-}"; total="$source_size"
    [ "$cap" = "$((end - start + 1))" ] && [ "$deadline" = 300 ] || return 96
    if [ "$scenario" = auth401 ] || [ "$scenario" = auth403 ]; then
        printf 'HTTP/2 %s\r\n\r\n' "${scenario#auth}" > "$header"
        return 22
    fi
    if [ "$scenario" = adaptive ] && [ "$calls" -le 3 ]; then
        printf 'HTTP/2 206\r\n\r\n' > "$header"
        printf 'URL_SECRET_MUST_BE_WITHHELD\n' >&2
        return 18
    fi
    if [ "$scenario" = interrupted ] && [ "$calls" -ge 2 ]; then
        printf 'HTTP/2 503\r\n\r\n' > "$header"
        return 18
    fi
    if [ "$end" -ge "$total" ]; then end=$((total - 1)); fi
    python3 - "$source_zip" "$body" "$start" "$end" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[2]).write_bytes(Path(sys.argv[1]).read_bytes()[int(sys.argv[3]):int(sys.argv[4])+1])
PY
    case "$scenario" in
        http200) printf 'HTTP/2 200\r\nContent-Range: bytes %s-%s/%s\r\n' "$start" "$end" "$total" > "$header" ;;
        wrongstart) printf 'HTTP/2 206\r\nContent-Range: bytes 1-%s/%s\r\n' "$end" "$total" > "$header" ;;
        changedtotal)
            if [ "$calls" -ge 2 ]; then total=$((total + 1)); fi
            printf 'HTTP/2 206\r\nContent-Range: bytes %s-%s/%s\r\n' "$start" "$end" "$total" > "$header" ;;
        badheader) printf 'HTTP/2 206\r\nContent-Range: URL_SECRET_MUST_BE_WITHHELD\r\n' > "$header" ;;
        *) printf 'HTTP/2 206\r\nContent-Range: bytes %s-%s/%s\r\n' "$start" "$end" "$total" > "$header" ;;
    esac
    if [ "$scenario" = adaptivebody ] && [ "$calls" -le 3 ]; then
        truncate -s 1 "$body"
    fi
    return 0
}
new_case() {
    scenario="$1"; calls=0
    case_dir="$TEST_DIR/$1"
    mkdir -m 700 "$case_dir"
    : > "$case_dir/cookies.txt"
    final="$case_dir/export.zip"
}
recover() { download_recover 'https://chatgpt.com/URL_SECRET_MUST_BE_WITHHELD' "$final" "$case_dir/cookies.txt" synthetic "$case_dir" "${1:-8}" "${2:-8}"; }
expect_success() {
    if ! recover "$@" > "$case_dir/diagnostics" 2>&1; then cat "$case_dir/diagnostics"; exit 1; fi
    cmp "$source_zip" "$final"
    [ ! -e "$final.part" ]
    [ -s "$case_dir/sync.calls" ]
}
expect_failure() {
    if recover "$@" > "$case_dir/diagnostics" 2>&1; then printf 'FAIL: expected rejection: %s\n' "$scenario"; exit 1; fi
}
new_case valid
expect_success
new_case resumed
head -c 7 "$source_zip" > "$final.part"
expect_success
[ "$(head -n1 "$case_dir/ranges")" = '7-14' ]
new_case invalidfinal
head -c 11 "$source_zip" > "$final"
expect_success
[ "$(head -n1 "$case_dir/ranges")" = '11-18' ]
new_case coexist
printf 'invalid final to preserve' > "$final"
head -c 7 "$source_zip" > "$final.part"
expect_success
backup=("$final".invalid.*)
[ "${#backup[@]}" -eq 1 ]
[ "$(cat "${backup[0]}")" = 'invalid final to preserve' ]
new_case interrupted
expect_failure
[ "$(stat -c %s "$final.part")" -eq 8 ]
scenario=valid; calls=0; : > "$case_dir/ranges"
expect_success
[ "$(head -n1 "$case_dir/ranges")" = '8-15' ]
for failure in http200 wrongstart badheader auth401 auth403; do
    new_case "$failure"
    expect_failure
    [ "$(stat -c %s "$final.part")" -eq 0 ]
    [ ! -e "$case_dir/sync.calls" ]
done
new_case changedtotal
expect_failure
[ "$(stat -c %s "$final.part")" -eq 8 ]
new_case adaptive
expect_success 134217728 16777216
printf '0-134217727\n0-67108863\n0-33554431\n0-16777215\n' > "$case_dir/expected.ranges"
cmp "$case_dir/expected.ranges" "$case_dir/ranges"
new_case explicit_inplace
head -c 13 "$source_zip" > "$final"
starting_hash="$(sha256sum "$final" | cut -d ' ' -f1)"
if ! download_recover 'https://chatgpt.com/synthetic' "$final" "$case_dir/cookies.txt" synthetic "$case_dir" 8 8 "$final" 13 "$starting_hash" > "$case_dir/diagnostics" 2>&1; then cat "$case_dir/diagnostics"; exit 1; fi
cmp "$source_zip" "$final"
[ ! -e "$final.part" ]
[ "$(head -n1 "$case_dir/ranges")" = '13-20' ]
new_case wronghash
head -c 13 "$source_zip" > "$final"
if download_recover 'https://chatgpt.com/synthetic' "$final" "$case_dir/cookies.txt" synthetic "$case_dir" 8 8 "$final" 13 "$(printf '%064d' 0)" > "$case_dir/diagnostics" 2>&1; then exit 1; fi
[ ! -e "$case_dir/ranges" ]
[ "$(stat -c %s "$final")" -eq 13 ]
new_case wrongsize
head -c 13 "$source_zip" > "$final"
if download_recover 'https://chatgpt.com/synthetic' "$final" "$case_dir/cookies.txt" synthetic "$case_dir" 8 8 "$final" 12 "$(printf '%064d' 0)" > "$case_dir/diagnostics" 2>&1; then exit 1; fi
[ ! -e "$case_dir/ranges" ]
[ "$(stat -c %s "$final")" -eq 13 ]
for failure in http200 wrongstart badheader auth401 auth403 lowdisk; do
    new_case "inplace_$failure"
    scenario="$failure"
    head -c 13 "$source_zip" > "$final"
    starting_hash="$(sha256sum "$final" | cut -d ' ' -f1)"
    if download_recover 'https://chatgpt.com/synthetic' "$final" "$case_dir/cookies.txt" synthetic "$case_dir" 8 8 "$final" 13 "$starting_hash" > "$case_dir/diagnostics" 2>&1; then exit 1; fi
    [ "$(sha256sum "$final" | cut -d ' ' -f1)" = "$starting_hash" ]
    [ ! -e "$final.part" ]
    [ ! -e "$case_dir/sync.calls" ]
done
new_case adaptivebody
expect_success 134217728 16777216
printf '0-134217727\n0-67108863\n0-33554431\n0-16777215\n' > "$case_dir/expected.ranges"
cmp "$case_dir/expected.ranges" "$case_dir/ranges"
new_case invalidzip
printf 'not a zip' > "$TEST_DIR/invalid-source"
source_zip="$TEST_DIR/invalid-source"; source_size=9
expect_failure
[ "$(stat -c %s "$final.part")" -eq 9 ]
[ ! -e "$final" ]
# Raw server headers, curl error bodies, URL configs and jars never enter logs.
if grep -q 'URL_SECRET_MUST_BE_WITHHELD' "$TEST_DIR"/*/diagnostics; then
    printf 'FAIL: secret appeared in diagnostics\n'; exit 1
fi
printf 'PASS: synthetic download, exact resume, invalid-final recovery, preservation, interruption, 200/wrong-range/changed-total rejection, 128/64/32/16 MiB retries, sync and invalid ZIP retention.\n'
