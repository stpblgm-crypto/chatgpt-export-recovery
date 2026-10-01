#!/usr/bin/env bash

# Browser-native Chromium CDP Range/checkpoint engine.
# Requires src/lib/download.sh to have been sourced first for:
#   validate_range_response, append_verified_segment, verify_zip_file

download_recover_cdp() {
    local url="${1-}"
    local final="${2-}"
    local work_dir="${3-}"
    local helper="${4-}"
    local chunk_bytes="${5:-8388608}"
    local min_chunk_bytes="${6:-1048576}"
    local part="${final}.part"
    local segment="$work_dir/download.segment.current"
    local headers="$work_dir/headers"
    local helper_error="$work_dir/cdp.error"
    local url_secret="$work_dir/url.secret"
    local output_dir start pos total segment_no current_chunk attempt
    local requested_end rc status content_range next free need margin
    local stamp saved expected_after validation_reason

    if [ -z "$url" ] || [ -z "$final" ] || [ ! -d "$work_dir" ] || [ ! -x "$helper" ]; then
        printf 'FAIL: Chromium CDP download engine received incomplete inputs\n' >&2
        return 2
    fi
    if [[ "$url" == *$'\n'* || "$url" == *$'\r'* ]]; then
        printf 'FAIL: signed URL contains invalid control characters.\n' >&2
        return 2
    fi
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
    printf '%s\n' "$url" > "$url_secret" || return 1
    chmod 600 "$url_secret" || return 1
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

    printf 'Segmented authenticated Chromium-CDP Range download\n'
    printf '  output=%s\n' "$final"
    printf '  checkpoint=%s\n' "$part"
    printf '  checkpoint_start=%s bytes\n' "$start"
    printf '  base_chunk=%s bytes\n' "$chunk_bytes"
    printf '  minimum_chunk=%s bytes\n' "$min_chunk_bytes"
    printf '  cookie_export=NO\n'

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

            rm -f -- "$segment" "$headers" "$helper_error"
            umask 077
            : > "$headers"
            : > "$helper_error"

            "$helper" \
                --url-file "$url_secret" \
                --start "$pos" \
                --end "$requested_end" \
                --output "$segment" \
                --headers "$headers" \
                2>"$helper_error"
            rc=$?

            status="$(awk '/^HTTP\// {gsub("\\r", "", $2); code=$2} END {print code}' "$headers")"
            content_range="$(awk 'tolower($0) ~ /^content-range:/ {sub(/^[^:]*:[[:space:]]*/, ""); gsub("\\r", ""); value=$0} END {print value}' "$headers")"
            printf '    transport_rc=%s http=%s\n' "$rc" "${status:-UNKNOWN}"
            printf '    content_range=%s\n' "${content_range:-MISSING}"

            if [ "$rc" -ne 0 ]; then
                rm -f -- "$segment"
                if [ "$status" = "401" ] || [ "$status" = "403" ]; then
                    printf 'AUTH/SIGNED-URL FAIL: browser network stack returned HTTP %s.\n' "$status" >&2
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
                    printf '    CDP transport failure; retrying same offset with %s bytes\n' "$next"
                    current_chunk="$next"
                    sleep 2
                    continue
                fi
                printf 'STOP: Chromium CDP transport unavailable at minimum chunk size; signed URL withheld.\n' >&2
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
                    printf 'STOP: insufficient free space. free=%s remaining=%s margin=%s\n' \
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
