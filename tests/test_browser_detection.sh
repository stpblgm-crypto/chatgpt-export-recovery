#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$REPO_ROOT/src/browsers/select.sh"
# Selection logic uses stubs, never an installed browser or a live profile.
firefox_present=false
chromium_present=false
firefox_detect_browser() { "$firefox_present"; }
chromium_detect_browser() { "$chromium_present"; }
unset CHATGPT_RECOVERY_FIREFOX_COOKIE_DB CHATGPT_RECOVERY_CHROMIUM_COOKIE_DB CHATGPT_RECOVERY_CHROMIUM_PROFILE
select_browser_provider firefox; [ "$SELECTED_BROWSER" = firefox ]
select_browser_provider chromium; [ "$SELECTED_BROWSER" = chromium ]
if select_browser_provider auto 2>/dev/null; then exit 1; fi
chromium_present=true
select_browser_provider auto; [ "$SELECTED_BROWSER" = chromium ]
firefox_present=true
select_browser_provider auto; [ "$SELECTED_BROWSER" = firefox ]
CHATGPT_RECOVERY_CHROMIUM_COOKIE_DB=synthetic
select_browser_provider auto; [ "$SELECTED_BROWSER" = chromium ]
CHATGPT_RECOVERY_FIREFOX_COOKIE_DB=synthetic
if select_browser_provider auto 2>/dev/null; then exit 1; fi
select_browser_provider chromium; [ "$SELECTED_BROWSER" = chromium ]
if select_browser_provider 'SECRET_UNSUPPORTED_BROWSER' 2> >(grep -qv SECRET); then exit 1; fi
bash "$REPO_ROOT/src/chatgpt-export-recover" --help | grep -q 'auto|firefox|chromium'
printf 'PASS: explicit, auto, precedence, missing and ambiguous browser selection.\n'
