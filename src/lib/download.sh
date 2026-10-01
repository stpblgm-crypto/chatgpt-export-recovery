#!/usr/bin/env bash

# Browser-independent, checkpointed HTTP Range download engine.
# This file is intended to be sourced by src/chatgpt-export-recover and tests.

RANGE_REMOTE_START=""
RANGE_REMOTE_END=""
RANGE_REMOTE_TOTAL=""
RANGE_BODY_BYTES=""
RANGE_VALIDATION_REASON=""

range_error() {
    printf 'RANGE VALIDATION FAIL: %s\n' "$*" >&2
}

range_reject() {
    RANGE_VALIDATION_REASON="${1-unknown}"
    shift || true
    range_error "$*"
    return 1
}

# Bound external integers before Bash arithmetic (including leading-zero/octal cases).
range_integer() {
    [[ "${1-}" =~ ^(0|[1-9][0-9]{0,17})$ ]]
}

require_bounded_curl() {
    local version major minor
    version="$(curl --disable --version 2>/dev/null | head -n1)"
    if [[ "$version" =~ ^curl[[:space:]]+([0-9]+)\.([0-9]+)\. ]]; then
        major="${BASH_REMATCH[1]}"; minor="${BASH_REMATCH[2]}"
        if [ "$major" -gt 8 ] || { [ "$major" -eq 8 ] && [ "$minor" -ge 4 ]; }; then
            return 0
        fi
    fi
    printf 'FAIL: curl 8.4.0 or newer is required to bound bodies without Content-Length.\n' >&2
    return 2
}

read_range_headers() {
    local headers="${1-}"
    # Reset at every response block so redirects cannot supply the final range.
    HTTP_STATUS="$(awk '/^HTTP\// {code=$2} END {gsub("\\r", "", code); if (code ~ /^[0-9][0-9][0-9]$/) print code; else print "INVALID"}' "$headers")"
    HTTP_CONTENT_RANGE="$(awk '
        /^HTTP\// {value=""; count=0}
        tolower($0) ~ /^content-range:/ {sub(/^[^:]*:[[:space:]]*/, ""); gsub("\\r", ""); value=$0; count++}
        END {if (count == 1) print value; else print "INVALID"}
    ' "$headers")"
}

validate_range_response() {
    local status="${1-}"
    local content_range="${2-}"
    local expected_start="${3-}"
    local requested_end="${4-}"
    local body_file="${5-}"
    local known_total="${6-}"
    local range_pattern='^[Bb][Yy][Tt][Ee][Ss][[:space:]]+([0-9]+)-([0-9]+)/([0-9]+)$'
    local remote_start remote_end remote_total actual expected

    RANGE_REMOTE_START=""
    RANGE_REMOTE_END=""
    RANGE_REMOTE_TOTAL=""
    RANGE_BODY_BYTES=""
    RANGE_VALIDATION_REASON=""

    if [ "$status" != "206" ]; then
        range_reject status "HTTP status is ${status:-missing}; expected 206"
        return 1
    fi

    if ! range_integer "$expected_start" ||
       ! range_integer "$requested_end" ||
       [ "$requested_end" -lt "$expected_start" ]; then
        range_reject request "invalid requested range"
        return 1
    fi

    if [[ "$content_range" =~ $range_pattern ]]; then
        remote_start="${BASH_REMATCH[1]}"
        remote_end="${BASH_REMATCH[2]}"
        remote_total="${BASH_REMATCH[3]}"
    else
        range_reject content-range "Content-Range is missing or malformed"
        return 1
    fi

    if ! range_integer "$remote_start" || ! range_integer "$remote_end" || ! range_integer "$remote_total"; then
        range_reject content-range "Content-Range contains unsafe integer values"
        return 1
    fi

    if [ "$remote_start" -ne "$expected_start" ]; then
        range_reject range-start "range starts at $remote_start; expected $expected_start"
        return 1
    fi
    if [ "$remote_end" -lt "$remote_start" ]; then
        range_reject range-end "range end is before range start"
        return 1
    fi
    if [ "$remote_end" -gt "$requested_end" ]; then
        range_reject range-end "range ends at $remote_end; requested at most $requested_end"
        return 1
    fi
    if [ "$remote_total" -le "$remote_end" ]; then
        range_reject remote-total "remote total $remote_total is inconsistent with end $remote_end"
        return 1
    fi
    if [ -n "$known_total" ]; then
        if ! range_integer "$known_total" || [ "$remote_total" -ne "$known_total" ]; then
            range_reject remote-total "remote total changed"
            return 1
        fi
    fi
    expected="$requested_end"
    if [ "$expected" -ge "$remote_total" ]; then
        expected=$((remote_total - 1))
    fi
    if [ "$remote_end" -ne "$expected" ]; then
        range_reject range-end "range does not reach the requested end or end of object"
        return 1
    fi
    if [ ! -f "$body_file" ]; then
        range_reject body-missing "segment body is missing"
        return 1
    fi

    actual="$(stat -c '%s' "$body_file" 2>/dev/null)" || {
        range_reject body-missing "cannot stat segment body"
        return 1
    }
    expected=$((remote_end - remote_start + 1))
    if [ "$actual" -ne "$expected" ]; then
        range_reject body-size "segment body size is $actual; expected $expected"
        return 1
    fi

    RANGE_REMOTE_START="$remote_start"
    RANGE_REMOTE_END="$remote_end"
    RANGE_REMOTE_TOTAL="$remote_total"
    RANGE_BODY_BYTES="$actual"
    return 0
}

append_verified_segment() {
    local part_file="${1-}"
    local segment_file="${2-}"
    local expected_before="${3-}"
    local expected_after="${4-}"
    local before after

    if [ ! -f "$part_file" ] || [ ! -f "$segment_file" ]; then
        printf 'APPEND FAIL: checkpoint or segment is missing\n' >&2
        return 1
    fi

    before="$(stat -c '%s' "$part_file" 2>/dev/null)" || return 1
    if [ "$before" -ne "$expected_before" ]; then
        printf 'APPEND FAIL: checkpoint changed before append\n' >&2
        return 1
    fi

    if ! command cat -- "$segment_file" >> "$part_file"; then
        truncate -s "$before" "$part_file" 2>/dev/null || true
        printf 'APPEND FAIL: write failed; checkpoint rollback attempted\n' >&2
        return 1
    fi

    after="$(stat -c '%s' "$part_file" 2>/dev/null)" || {
        truncate -s "$before" "$part_file" 2>/dev/null || true
        return 1
    }
    if [ "$after" -ne "$expected_after" ]; then
        truncate -s "$before" "$part_file" 2>/dev/null || true
        printf 'APPEND FAIL: resulting checkpoint has unexpected size\n' >&2
        return 1
    fi

    if ! sync -f "$part_file" 2>/dev/null; then
        if ! sync 2>/dev/null; then
            truncate -s "$before" "$part_file" 2>/dev/null || true
            sync 2>/dev/null || true
            printf 'APPEND FAIL: filesystem sync failed; checkpoint rollback attempted\n' >&2
            return 1
        fi
    fi
    return 0
}

verify_zip_file() {
    local archive="${1-}"
    if command -v unzip >/dev/null 2>&1; then
        unzip -tq "$archive" >/dev/null 2>&1
    else
        python3 - "$archive" >/dev/null 2>&1 <<'PYZIP'
import sys
import zipfile
try:
    with zipfile.ZipFile(sys.argv[1]) as archive:
        valid = archive.testzip() is None
except Exception:
    valid = False
raise SystemExit(0 if valid else 1)
PYZIP
    fi
}

download_recover() {
    local url="${1-}"
    local final="${2-}"
    local cookie_jar="${3-}"
    local user_agent="${4-}"
    local work_dir="${5-}"
    local chunk_bytes="${6:-134217728}"
    local min_chunk_bytes="${7:-16777216}"
    local checkpoint_override="${8-}"
    local expected_size="${9-}"
    local expected_sha256="${10-}"
    local transport="${11:-curl}"
    local transport_profile="${12:-chrome}"
    local part="${checkpoint_override:-${final}.part}"
    local segment="$work_dir/download.segment.current"
    local headers="$work_dir/headers"
    local curl_error="$work_dir/curl.error"
    local url_config="$work_dir/curl-url.conf"
    local output_dir start pos total segment_no current_chunk attempt
    local requested_end rc status content_range next free need margin
    local stamp saved expected_after validation_reason actual_hash
    local transport_python transport_helper

    if [ -z "$url" ] || [ -z "$final" ] || [ ! -f "$cookie_jar" ] || [ ! -d "$work_dir" ]; then
        printf 'FAIL: download engine received incomplete inputs\n' >&2
        return 2
    fi
    if [[ "$url" == *$'\n'* || "$url" == *$'\r'* || "$url" == *'"'* ]]; then
        printf 'FAIL: signed URL contains characters unsafe for the private curl config.\n' >&2
        return 2
    fi
    case "$url" in
        *\\*)
            printf 'FAIL: signed URL contains characters unsafe for the private curl config.\n' >&2
            return 2
            ;;
    esac
    if ! range_integer "$chunk_bytes" ||
       ! range_integer "$min_chunk_bytes" ||
       [ "$chunk_bytes" -lt "$min_chunk_bytes" ] ||
       [ "$min_chunk_bytes" -le 0 ]; then
        printf 'FAIL: invalid chunk-size configuration\n' >&2
        return 2
    fi

    case "$transport" in
        curl)
            require_bounded_curl || return 2
            ;;
        curl-cffi)
            transport_python="${CHATGPT_RECOVERY_CURL_CFFI_PYTHON:-python3}"
            if ! "$transport_python" -c 'import curl_cffi' >/dev/null 2>&1; then
                printf 'FAIL: curl-cffi transport requires the optional curl_cffi package.\n' >&2
                return 2
            fi
            if [ ! -f "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/transports/curl_cffi_range.py" ]; then
                printf 'FAIL: curl-cffi transport helper is missing.\n' >&2
                return 2
            fi
            ;;
        *)
            printf 'FAIL: unsupported transport.\n' >&2
            return 2
            ;;
    esac

    output_dir="$(dirname -- "$final")"
    if [ ! -d "$output_dir" ]; then
        printf 'FAIL: output directory does not exist: %s\n' "$output_dir" >&2
        return 2
    fi
    if [ -d "$final" ] || [ -d "$part" ]; then
        printf 'FAIL: output or checkpoint path is a directory\n' >&2
        return 2
    fi
    if [ -L "$final" ] || [ -L "$part" ]; then
        printf 'FAIL: refusing symlink output or checkpoint path\n' >&2
        return 2
    fi

    if [ -n "$checkpoint_override" ]; then
        if [ ! -f "$part" ] || ! range_integer "$expected_size" ||
           [[ ! "$expected_sha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
            printf 'FAIL: explicit checkpoint requires an existing file and expected size/SHA256.\n' >&2
            return 2
        fi
        if [ "$(stat -c '%s' "$part" 2>/dev/null)" != "$expected_size" ]; then
            printf 'FAIL: checkpoint size does not match the approved starting size.\n' >&2
            return 2
        fi
        actual_hash="$(sha256sum -- "$part")" || return 1
        actual_hash="${actual_hash%% *}"
        if [ "$actual_hash" != "${expected_sha256,,}" ]; then
            printf 'FAIL: checkpoint SHA256 does not match the approved starting hash.\n' >&2
            return 2
        fi
        # Normalize lexical paths so --output ./a --checkpoint-file a is in-place.
        part="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$part")" || return 1
        final="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$final")" || return 1
        if [ "$part" != "$final" ] && [ "$part" -ef "$final" ]; then
            printf 'FAIL: output and explicit checkpoint alias the same file.\n' >&2
            return 2
        fi
        printf 'Starting checkpoint size and SHA256 verified; original filename preserved.\n'
    fi

    umask 077
    printf 'url = "%s"\n' "$url" > "$url_config" || return 1
    chmod 600 "$url_config" || return 1
    url=""

    if [ -f "$final" ]; then
        if verify_zip_file "$final"; then
            printf 'PASS: final ZIP already exists and is valid.\n'
            stat -c 'size=%s bytes' "$final"
            sha256sum "$final"
            return 0
        fi

        if [ "$part" = "$final" ]; then
            printf 'Using invalid final file as an explicit in-place checkpoint.\n'
        elif [ -f "$part" ]; then
            stamp="$(date -u +%Y%m%dT%H%M%SZ)"
            saved="${final}.invalid.${stamp}"
            if [ -e "$saved" ]; then
                printf 'FAIL: refusing to overwrite existing invalid-file backup: %s\n' "$saved" >&2
                return 1
            fi
            mv -- "$final" "$saved" || return 1
            printf 'Preserved an additional invalid final file at: %s\n' "$saved"
        else
            mv -- "$final" "$part" || return 1
            printf 'Moved invalid final file into resumable checkpoint: %s\n' "$part"
        fi
    fi

    if [ -f "$part" ]; then
        start="$(stat -c '%s' "$part" 2>/dev/null)" || return 1
    else
        umask 077
        : > "$part" || return 1
        start=0
    fi

    printf 'Segmented authenticated Range download\n'
    printf '  output=%s\n' "$final"
    printf '  checkpoint=%s\n' "$part"
    printf '  checkpoint_start=%s bytes\n' "$start"
    printf '  base_chunk=%s bytes\n' "$chunk_bytes"
    printf '  minimum_chunk=%s bytes\n' "$min_chunk_bytes"

    local -a curl_common=(
        --location
        --fail
        --silent
        --show-error
        --connect-timeout 30
        --max-time 300
        --max-redirs 5
        --globoff
        --speed-time 120
        --speed-limit 1024
        --proto '=https'
        --proto-redir '=https'
        --cookie "$cookie_jar"
        --cookie-jar "$cookie_jar"
        --user-agent "$user_agent"
        --header 'Referer: https://chatgpt.com/'
        --header 'Accept: */*'
        --header 'Accept-Encoding: identity'
    )

    pos="$start"
    total=""
    segment_no=0

    while :; do
        if [ -n "$total" ] && [ "$pos" -ge "$total" ]; then
            break
        fi

        segment_no=$((segment_no + 1))
        current_chunk="$chunk_bytes"
        attempt=0

        while :; do
            attempt=$((attempt + 1))
            requested_end=$((pos + current_chunk - 1))
            if [ -n "$total" ] && [ "$requested_end" -ge "$total" ]; then
                requested_end=$((total - 1))
            fi

            printf '  segment #%s attempt #%s: bytes=%s-%s\n' \
                "$segment_no" "$attempt" "$pos" "$requested_end"

            rm -f -- "$segment" "$headers" "$curl_error"
            umask 077
            : > "$headers"
            : > "$curl_error"

            if [ "$transport" = "curl" ]; then
                curl --disable --config "$url_config" "${curl_common[@]}" \
                    --range "${pos}-${requested_end}" \
                    --max-filesize "$((requested_end - pos + 1))" \
                    --dump-header "$headers" \
                    --output "$segment" \
                    2>"$curl_error"
                rc=$?
                read_range_headers "$headers"
                status="$HTTP_STATUS"
                content_range="$HTTP_CONTENT_RANGE"
            else
                transport_python="${CHATGPT_RECOVERY_CURL_CFFI_PYTHON:-python3}"
                transport_helper="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/transports/curl_cffi_range.py"
                "$transport_python" "$transport_helper" \
                    --url-config "$url_config" \
                    --cookie-jar "$cookie_jar" \
                    --range "${pos}-${requested_end}" \
                    --output "$segment" \
                    --meta "$headers" \
                    --impersonate "$transport_profile" \
                    2>"$curl_error"
                rc=$?
                status="$(awk -F= '$1=="http"{print $2}' "$headers" 2>/dev/null)"
                content_range="$(awk -F= '$1=="content_range"{sub(/^[^=]*=/,""); print}' "$headers" 2>/dev/null)"
                [ -n "$status" ] || status="INVALID"
                [ -n "$content_range" ] || content_range="INVALID"
            fi

            printf '    transport=%s rc=%s http=%s\n' "$transport" "$rc" "${status:-UNKNOWN}"
            # Transport diagnostics never print cookie values, signed URLs, or raw headers.

            if [ "$rc" -ne 0 ]; then
                rm -f -- "$segment"
                if [ "$status" = "200" ]; then
                    printf 'STOP: HTTP 200 cannot be appended; checkpoint unchanged.\n' >&2
                    return 24
                fi
                if [ "$status" = "401" ] || [ "$status" = "403" ]; then
                    printf 'AUTH/SIGNED-URL FAIL: server returned HTTP %s.\n' "$status" >&2
                    printf 'Resume position: %s\n' "$pos" >&2
                    return 22
                fi
                if [[ "$status" =~ ^4[0-9][0-9]$ ]]; then
                    printf 'STOP: non-retryable HTTP %s; checkpoint unchanged.\n' "$status" >&2
                    return 22
                fi
                if [ "$current_chunk" -gt "$min_chunk_bytes" ]; then
                    next=$((current_chunk / 2))
                    if [ "$next" -lt "$min_chunk_bytes" ]; then
                        next="$min_chunk_bytes"
                    fi
                    printf '    transport failure; retrying same offset with %s bytes\n' "$next"
                    current_chunk="$next"
                    sleep 2
                    continue
                fi
                printf 'STOP: transport failure at minimum chunk size; details withheld to protect the signed URL.\n' >&2
                printf 'Resume position: %s\n' "$pos" >&2
                return "$rc"
            fi

            if ! validate_range_response \
                "$status" "$content_range" "$pos" "$requested_end" "$segment" "$total"; then
                validation_reason="$RANGE_VALIDATION_REASON"
                rm -f -- "$segment"
                if [ "$validation_reason" = "body-size" ] && [ "$current_chunk" -gt "$min_chunk_bytes" ]; then
                    next=$((current_chunk / 2))
                    if [ "$next" -lt "$min_chunk_bytes" ]; then
                        next="$min_chunk_bytes"
                    fi
                    printf '    body-length failure; retrying same offset with %s bytes\n' "$next"
                    current_chunk="$next"
                    sleep 2
                    continue
                fi
                printf 'Checkpoint remains unchanged at %s bytes.\n' "$pos" >&2
                return 24
            fi

            printf '    content_range=bytes %s-%s/%s\n' \
                "$RANGE_REMOTE_START" "$RANGE_REMOTE_END" "$RANGE_REMOTE_TOTAL"
            if [ -z "$total" ]; then
                total="$RANGE_REMOTE_TOTAL"
                printf '    remote_total=%s bytes\n' "$total"

                free="$(df -PB1 "$(dirname -- "$part")" 2>/dev/null | awk 'NR==2 {print $4}')"
                need=$((total - pos))
                margin=$((1024 * 1024 * 1024))
                if ! range_integer "$free"; then
                    rm -f -- "$segment"
                    printf 'STOP: cannot verify free checkpoint disk space; checkpoint unchanged.\n' >&2
                    return 28
                fi
                if [ "$free" -lt $((need + margin)) ]; then
                    rm -f -- "$segment"
                    printf 'STOP: insufficient free space. free=%s needed=%s margin=%s\n' \
                        "$free" "$need" "$margin" >&2
                    return 28
                fi
            fi

            expected_after=$((RANGE_REMOTE_END + 1))
            printf '    validation=PASS; downloaded=%s; appending\n' "$RANGE_BODY_BYTES"
            if ! append_verified_segment "$part" "$segment" "$pos" "$expected_after"; then
                rm -f -- "$segment"
                return 1
            fi
            rm -f -- "$segment"
            pos="$expected_after"
            printf '    checkpoint=%s / %s bytes\n\n' "$pos" "$total"
            break
        done
    done

    if [ -z "$total" ] || [ "$pos" -ne "$total" ]; then
        printf 'FAIL: assembled size does not equal the frozen remote total.\n' >&2
        return 1
    fi

    printf 'Verifying ZIP integrity...\n'
    if verify_zip_file "$part"; then
        if [ "$part" != "$final" ]; then
            mv -- "$part" "$final" || return 1
        fi
        printf 'PASS: archive is complete and ZIP-valid.\n'
        stat -c 'size=%s bytes' "$final"
        sha256sum "$final"
        printf 'FINAL=%s\n' "$final"
        return 0
    fi

    printf 'ZIP TEST: FAIL. Verified HTTP segments remain at: %s\n' "$part" >&2
    printf 'No automatic full redownload was started.\n' >&2
    return 4
}
