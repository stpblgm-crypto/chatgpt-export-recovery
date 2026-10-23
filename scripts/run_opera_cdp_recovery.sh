#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${REPO:-/home/alex/chatgpt-export-recovery-cdp}"
OPERA_BIN="${OPERA_BIN:-/usr/lib/x86_64-linux-gnu/opera-stable/opera}"
OPERA_USER_DATA="${OPERA_USER_DATA:-/home/alex/.config/opera}"
OPERA_PROFILE="${OPERA_PROFILE:-Default}"
CDP_PORT="${CDP_PORT:-9223}"

URL_FILE="${URL_FILE:-/home/alex/.hydra_private/chatgpt_export_url_recovery_20261001.txt}"
FINAL="${FINAL:-/home/alex/Downloads/b18fe3547bce7cb708b7fa71de651649c326d6b7d822a45b2f968a4d8b795663-2026-09-29-16-57-20-6094fffeb0314c4080e1af2abf272987.IJHW42mb.zip}"
PART="${FINAL}.part"

EXPECTED_PREFIX_SIZE="${EXPECTED_PREFIX_SIZE:-19426525184}"
EXPECTED_PREFIX_SHA256="${EXPECTED_PREFIX_SHA256:-4f7d70f5d6bfab53a7c7d363a76fba94cf054f8aaca2ec8b797509d7eee34308}"
EXPECTED_REMOTE_TOTAL="${EXPECTED_REMOTE_TOTAL:-32841707548}"
CHUNK_SIZE="${CHUNK_SIZE:-67108864}"
MIN_CHUNK_SIZE="${MIN_CHUNK_SIZE:-1048576}"

LOG="${LOG:-/home/alex/Downloads/CHATGPT_EXPORT_TERMINAL_RECOVERY_20261001.log}"
OPERA_LOG="${OPERA_LOG:-/home/alex/Downloads/CHATGPT_EXPORT_OPERA_CDP_20261001.log}"

exec > >(tee -a "$LOG") 2>&1

echo "=== ChatGPT export terminal recovery ==="
echo "started=$(date --iso-8601=seconds)"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "HOLD: missing command: $1"
    exit 2
  }
}

for x in bash python3 sha256sum stat ss unzip; do
  need "$x"
done

test -d "$REPO" || { echo "HOLD: repo/worktree missing: $REPO"; exit 3; }
test -x "$OPERA_BIN" || { echo "HOLD: Opera binary missing: $OPERA_BIN"; exit 4; }
test -f "$URL_FILE" || { echo "HOLD: private signed URL file missing"; exit 5; }
test -f "$PART" || { echo "HOLD: trusted checkpoint missing: $PART"; exit 6; }

url_mode=$(stat -c %a "$URL_FILE")
test "$url_mode" = "600" || {
  echo "HOLD: URL file must be mode 600; got $url_mode"
  exit 7
}

prefix_size=$(stat -c %s "$PART")
test "$prefix_size" = "$EXPECTED_PREFIX_SIZE" || {
  echo "HOLD: checkpoint size mismatch: $prefix_size != $EXPECTED_PREFIX_SIZE"
  exit 8
}

echo "checkpoint_size=PASS $prefix_size"
prefix_sha=$(sha256sum "$PART" | awk '{print $1}')
test "$prefix_sha" = "$EXPECTED_PREFIX_SHA256" || {
  echo "HOLD: checkpoint SHA256 mismatch"
  exit 9
}
echo "checkpoint_sha256=PASS"

free=$(df -PB1 "$(dirname "$FINAL")" | awk 'NR==2 {print $4}')
remaining=$((EXPECTED_REMOTE_TOTAL - prefix_size))
margin=$((1024 * 1024 * 1024))
test "$free" -ge $((remaining + margin)) || {
  echo "HOLD: insufficient free space; free=$free remaining=$remaining margin=$margin"
  exit 10
}
echo "disk_gate=PASS free=$free remaining=$remaining"

# Keep the current trusted checkpoint immutable until the browser-native
# recovery engine itself validates and appends exact HTTP 206 ranges.
chmod u+x "$REPO/src/browsers/chromium_cdp_fetch.py" || true

echo "Stopping Opera gracefully so the authenticated profile can be relaunched with local CDP..."
mapfile -t opera_pids < <(
  pgrep -f '^/usr/lib/x86_64-linux-gnu/opera-stable/opera( |$)' || true
)
if (("${#opera_pids[@]}" > 0)); then
  kill -TERM "${opera_pids[@]}" 2>/dev/null || true
  for _ in {1..20}; do
    alive=0
    for pid in "${opera_pids[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then alive=1; break; fi
    done
    ((alive == 0)) && break
    sleep 0.5
  done
fi

uid=$(id -u)
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$uid}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$uid/bus}"

echo "Starting Opera with localhost-only CDP on port $CDP_PORT..."
nohup "$OPERA_BIN"   --remote-debugging-address=127.0.0.1   --remote-debugging-port="$CDP_PORT"   --user-data-dir="$OPERA_USER_DATA"   --profile-directory="$OPERA_PROFILE"   --no-first-run   --restore-last-session   https://chatgpt.com/   >"$OPERA_LOG" 2>&1 &

ready=0
for _ in {1..60}; do
  if ss -ltn | grep -q "127.0.0.1:$CDP_PORT"; then
    ready=1
    break
  fi
  sleep 0.5
done
test "$ready" = "1" || {
  echo "HOLD: Opera CDP port did not become ready"
  exit 11
}
echo "cdp_port=READY"

python3 - "$CDP_PORT" <<'PY'
import json,sys,time,urllib.request,urllib.parse
port=int(sys.argv[1])
deadline=time.time()+30
last=None
while time.time()<deadline:
    try:
        rows=json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list",timeout=1))
        ok=False
        for row in rows:
            if row.get("type")!="page" or not row.get("webSocketDebuggerUrl"):
                continue
            u=urllib.parse.urlsplit(row.get("url",""))
            host=(u.hostname or "").lower()
            if host=="chatgpt.com" or host.endswith(".chatgpt.com") or host=="openai.com" or host.endswith(".openai.com"):
                ok=True
                break
        if ok:
            print("cdp_chatgpt_target=PASS")
            raise SystemExit(0)
    except Exception as e:
        last=type(e).__name__
    time.sleep(0.5)
print("HOLD: no authenticated ChatGPT/OpenAI page target on CDP", last or "")
raise SystemExit(12)
PY

echo "Starting browser-native Range resume..."
export CHATGPT_RECOVERY_CDP_ENDPOINT="http://127.0.0.1:$CDP_PORT"
bash "$REPO/src/chatgpt-export-recover"   --browser chromium-cdp   --url-file "$URL_FILE"   --output "$FINAL"   --chunk-size "$CHUNK_SIZE"   --min-chunk-size "$MIN_CHUNK_SIZE"

test -f "$FINAL" || {
  echo "HOLD: recovery command returned without final ZIP"
  exit 13
}

final_size=$(stat -c %s "$FINAL")
test "$final_size" = "$EXPECTED_REMOTE_TOTAL" || {
  echo "HOLD: final size mismatch: $final_size != $EXPECTED_REMOTE_TOTAL"
  exit 14
}

echo "Running independent ZIP integrity verification..."
unzip -t "$FINAL" >/dev/null
echo "ZIP_INTEGRITY=PASS"

final_sha=$(sha256sum "$FINAL" | awk '{print $1}')
echo "FINAL_SIZE=$final_size"
echo "FINAL_SHA256=$final_sha"
echo "FINAL=$FINAL"
echo "DONE $(date --iso-8601=seconds)"
