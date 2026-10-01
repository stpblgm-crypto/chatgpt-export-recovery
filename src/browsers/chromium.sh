#!/usr/bin/env bash

# EXPERIMENTAL / NOT LIVE VERIFIED. No CDP, custom crypto, or login fallback.
# Keyring use is possible only through the explicitly selected optional adapter.
CHROMIUM_PROVIDER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

chromium_detect_browser() {
    python3 "$CHROMIUM_PROVIDER_DIR/chromium_cookies.py" detect >/dev/null 2>&1
}

chromium_user_agent() {
    # A real browser UA can be supplied through an explicitly permitted runtime.
    # Do not launch or inspect a browser to obtain it.
    local user_agent="${CHATGPT_RECOVERY_CHROMIUM_USER_AGENT:-chatgpt-export-recovery/experimental-chromium}"
    if [[ "$user_agent" == *$'\r'* || "$user_agent" == *$'\n'* ]]; then
        printf 'ERROR: user agent contains unsafe control characters.\n' >&2
        return 2
    fi
    printf '%s\n' "$user_agent"
}

chromium_build_temporary_cookie_jar() {
    local jar="${1-}" meta="${2-}" work="${3-}"
    if [ -z "$jar" ] || [ -z "$meta" ] || [ ! -d "$work" ]; then
        printf 'ERROR: Chromium provider received incomplete inputs.\n' >&2
        return 2
    fi
    python3 "$CHROMIUM_PROVIDER_DIR/chromium_cookies.py" build "$jar" "$meta" "$work"
}

chromium_cleanup_session_material() {
    local work="${1-}"
    case "$work" in
        */chatgpt-export-recovery.*)
            [ ! -d "$work" ] || rm -rf -- "$work"
            ;;
        *)
            printf 'ERROR: refusing to clean an unexpected runtime path.\n' >&2
            return 1
            ;;
    esac
}
