#!/usr/bin/env bash

# Keep Firefox first for backward compatibility. Detection is not authentication.
select_browser_provider() {
    local requested="${1:-auto}"
    SELECTED_BROWSER=""
    case "$requested" in
        firefox|chromium) SELECTED_BROWSER="$requested" ;;
        auto)
            if [ -n "${CHATGPT_RECOVERY_FIREFOX_COOKIE_DB:-}" ] && { [ -n "${CHATGPT_RECOVERY_CHROMIUM_COOKIE_DB:-}" ] || [ -n "${CHATGPT_RECOVERY_CHROMIUM_PROFILE:-}" ]; }; then
                printf 'ERROR: both provider overrides are set; choose --browser explicitly.\n' >&2
                return 2
            elif [ -n "${CHATGPT_RECOVERY_FIREFOX_COOKIE_DB:-}" ]; then
                SELECTED_BROWSER=firefox
            elif { [ -n "${CHATGPT_RECOVERY_CHROMIUM_COOKIE_DB:-}" ] || [ -n "${CHATGPT_RECOVERY_CHROMIUM_PROFILE:-}" ]; }; then
                SELECTED_BROWSER=chromium
            elif firefox_detect_browser; then
                SELECTED_BROWSER=firefox
            elif chromium_detect_browser; then
                SELECTED_BROWSER=chromium
            else
                printf 'ERROR: no supported browser profile was detected.\n' >&2
                return 1
            fi
            ;;
        *)
            printf 'ERROR: browser provider must be auto, firefox, or chromium.\n' >&2
            return 2
            ;;
    esac
}
