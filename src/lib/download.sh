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

    if [[ ! "$expected_start" =~ ^[0-9]+$ ]] ||
       [[ ! "$requested_end" =~ ^[0-9]+$ ]] ||
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
        if [[ ! "$known_total" =~ ^[0-9]+$ ]] || [ "$remote_total" -ne "$known_total" ]; then
            range_reject remote-total "remote total changed"
            return 1
        fi
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
        python3 -m zipfile -t "$archive" >/dev/null 2>&1
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
    local part="${final}.part"
    local segment="$work_dir/download.segment.current"
    local headers="$work_dir/headers"
    local curl_error="$work_dir/curl.error"
    local url_config="$work_dir/curl-url.conf"
    local output_dir start pos total segment_no current_chunk attempt
    local requested_end rc status content_range next free need margin
    local stamp saved expected_after validation_reason

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
    if [[ ! "$chunk_bytes" =~ ^[0-9]+$ ]] ||
       [[ ! "$min_chunk_bytes" =~ ^[0-9]+$ ]] ||
       [ "$chunk_bytes" -lt "$min_chunk_bytes" ] ||
       [ "$min_chunk_bytes" -le 0 ]; then
        printf 'FAIL: invalid chunk-size configuration\n' >&2
        return 2
    fi

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

        if [ -f "$part" ]; then
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

            curl --disable --config "$url_config" "${curl_common[@]}" \
                --range "${pos}-${requested_end}" \
                --dump-header "$headers" \
                --output "$segment" \
                2>"$curl_error"
            rc=$?

            status="$(awk '/^HTTP\// {gsub("\\r", "", $2); code=$2} END {print code}' "$headers")"
            content_range="$(awk 'tolower($0) ~ /^content-range:/ {sub(/^[^:]*:[[:space:]]*/, ""); gsub("\\r", ""); value=$0} END {print value}' "$headers")"
            printf '    curl_rc=%s http=%s\n' "$rc" "${status:-UNKNOWN}"
            printf '    content_range=%s\n' "${content_range:-MISSING}"

            if [ "$rc" -ne 0 ]; then
                rm -f -- "$segment"
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

            if [ -z "$total" ]; then
                total="$RANGE_REMOTE_TOTAL"
                printf '    remote_total=%s bytes\n' "$total"

                free="$(df -PB1 "$output_dir" 2>/dev/null | awk 'NR==2 {print $4}')"
                need=$((total - pos))
                margin=$((1024 * 1024 * 1024))
                if [ -n "$free" ] && [ "$free" -lt $((need + margin)) ]; then
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
        mv -- "$part" "$final" || return 1
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
