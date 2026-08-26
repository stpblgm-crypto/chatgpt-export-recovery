#!/usr/bin/env bash
# ChatGPT export recovery V4
# Fixes V3 stage-1 hang:
# - no broad recursive scan of ~/snap/firefox
# - hard 4s timeout around SQLite online backup
# - fallback copy of cookies.sqlite + WAL + SHM
# - progress output for every candidate
# - existing export partial is never modified until HTTP 206/Content-Range validation succeeds

URL='__CHATGPT_EXPORT_SIGNED_URL__'
DIR='/home/alex/Downloads'
NAME='b18fe3547bce7cb708b7fa71de651649c326d6b7d822a45b2f968a4d8b795663-2026-08-25-10-24-48-7fa9b2e9f7bd4872b4d8000b4a9aa821.zip'

FINAL="$DIR/$NAME"
PART="$FINAL.part"
STAMP="$(date +%Y%m%d_%H%M%S)"

RUNTIME_BASE="${XDG_RUNTIME_DIR:-/tmp}"
WORK="$(mktemp -d "$RUNTIME_BASE/chatgpt-export-v4.XXXXXX")" || exit 2
chmod 700 "$WORK"
COOKIEJAR="$WORK/cookies.txt"
COOKIE_META="$WORK/cookie_meta.txt"
CHUNK="$FINAL.segment.current"
HEADERS="$WORK/headers.txt"
CHUNK_BYTES=$((128 * 1024 * 1024))
MIN_CHUNK_BYTES=$((16 * 1024 * 1024))

cleanup() {
    rm -rf -- "$WORK" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

zip_test() {
    if command -v unzip >/dev/null 2>&1; then
        unzip -tq "$1" >/dev/null 2>&1
    else
        python3 -m zipfile -t "$1" >/dev/null 2>&1
    fi
}

die() {
    echo
    echo "FAIL: $*"
    exit 1
}

echo "=== ChatGPT export recovery V6 / Firefox live session ==="
echo "Target: $FINAL"
echo

command -v curl >/dev/null 2>&1 || die "curl не найден"
command -v python3 >/dev/null 2>&1 || die "python3 не найден"
[ -d "$DIR" ] || die "каталог $DIR не существует"

FFVER="$(firefox --version 2>/dev/null | grep -oE '[0-9]+([.][0-9]+)+' | tail -n1 || true)"
[ -n "$FFVER" ] || FFVER="154.0"
UA="Mozilla/5.0 (X11; Linux x86_64; rv:${FFVER}) Gecko/20100101 Firefox/${FFVER}"

echo "1/4 Поиск Firefox profile и session cookies..."
echo "    (каждый SQLite online-backup ограничен 4 секундами)"

COOKIEJAR="$COOKIEJAR" COOKIE_META="$COOKIE_META" WORK="$WORK" python3 <<'PY'
import configparser
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
jar_path = Path(os.environ["COOKIEJAR"])
meta_path = Path(os.environ["COOKIE_META"])
work = Path(os.environ["WORK"])

candidates = []
seen = set()

def add_db(p, source):
    try:
        p = Path(p)
        if p.name != "cookies.sqlite" or not p.is_file():
            return
        rp = p.resolve()
        key = str(rp)
        if key in seen:
            return
        seen.add(key)
        candidates.append((rp, source))
    except Exception:
        return

# 1) Strongest signal: DB currently opened by running Firefox.
for pd in Path("/proc").glob("[0-9]*"):
    try:
        cmd = (pd / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore").lower()
    except Exception:
        continue
    if "firefox" not in cmd:
        continue
    try:
        fds = list((pd / "fd").iterdir())
    except Exception:
        continue
    for fd in fds:
        try:
            s = os.readlink(fd)
        except Exception:
            continue
        if s.endswith("/cookies.sqlite"):
            add_db(Path(s), "open-by-firefox")
        elif s.endswith("/cookies.sqlite-wal") or s.endswith("/cookies.sqlite-shm"):
            add_db(Path(s.rsplit("-", 1)[0]), "open-by-firefox")

# 2) profiles.ini, standard Firefox layouts.
roots = [
    HOME / "snap/firefox/common/.mozilla/firefox",
    HOME / ".mozilla/firefox",
    HOME / ".var/app/org.mozilla.firefox/.mozilla/firefox",
]
for root in roots:
    ini = root / "profiles.ini"
    if not ini.is_file():
        continue
    cp = configparser.RawConfigParser()
    try:
        cp.read(ini, encoding="utf-8")
        for sec in cp.sections():
            if not sec.startswith("Profile"):
                continue
            raw = cp.get(sec, "Path", fallback="").strip()
            if not raw:
                continue
            rel = cp.getboolean(sec, "IsRelative", fallback=True)
            profile = root / raw if rel else Path(raw).expanduser()
            add_db(profile / "cookies.sqlite", "profiles.ini")
    except Exception:
        pass

# 3) Shallow fallback only. No recursive ~/snap/firefox scan.
for root in roots:
    if not root.is_dir():
        continue
    try:
        for child in root.iterdir():
            if child.is_dir():
                add_db(child / "cookies.sqlite", "profile-dir")
    except Exception:
        pass

if not candidates:
    print("ERROR: Firefox cookies.sqlite не найден.", flush=True)
    raise SystemExit(3)

print(f"    найдено candidate DB: {len(candidates)}", flush=True)

backup_code = """
import sqlite3, sys
srcp, dstp = sys.argv[1], sys.argv[2]
src = sqlite3.connect("file:" + srcp + "?mode=ro", uri=True, timeout=1)
dst = sqlite3.connect(dstp)
try:
    src.backup(dst, pages=256, sleep=0.05)
finally:
    dst.close()
    src.close()
"""

def snapshot(db: Path, idx: int):
    online = work / f"online_{idx}.sqlite"
    try:
        subprocess.run(
            [sys.executable, "-c", backup_code, str(db), str(online)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
            check=True,
        )
        return online, "online-backup"
    except Exception:
        try:
            online.unlink(missing_ok=True)
        except Exception:
            pass

    # Fallback: private copy DB + WAL + SHM.
    snapdir = work / f"copy_{idx}"
    snapdir.mkdir(mode=0o700, exist_ok=True)
    target = snapdir / "cookies.sqlite"

    wal = Path(str(db) + "-wal")
    shm = Path(str(db) + "-shm")

    if wal.is_file():
        try:
            shutil.copy2(wal, Path(str(target) + "-wal"))
        except Exception:
            pass

    shutil.copy2(db, target)

    if wal.is_file():
        try:
            shutil.copy2(wal, Path(str(target) + "-wal"))
        except Exception:
            pass

    if shm.is_file():
        try:
            shutil.copy2(shm, Path(str(target) + "-shm"))
        except Exception:
            pass

    return target, "db+wal-copy"

results = []
now = int(time.time())

for idx, (db, source) in enumerate(candidates, 1):
    print(f"    [{idx}/{len(candidates)}] {db.parent} ({source})", flush=True)
    try:
        snap, method = snapshot(db, idx)
        con = sqlite3.connect(snap, timeout=2)
        try:
            total = int(con.execute("SELECT COUNT(*) FROM moz_cookies").fetchone()[0])
            cols = {r[1] for r in con.execute("PRAGMA table_info(moz_cookies)")}
            access = "lastAccessed" if "lastAccessed" in cols else "0"
            origin = "originAttributes" if "originAttributes" in cols else "''"

            all_match = int(con.execute("""
                SELECT COUNT(*) FROM moz_cookies
                 WHERE lower(host) LIKE '%chatgpt.com'
                    OR lower(host) LIKE '%openai.com'
            """).fetchone()[0])

            rows = list(con.execute(f"""
                SELECT host, path, name, value, expiry, isSecure, isHttpOnly,
                       {access}, {origin}
                  FROM moz_cookies
                 WHERE (
                        lower(host) = 'chatgpt.com'
                     OR lower(host) LIKE '%.chatgpt.com'
                     OR lower(host) = 'openai.com'
                     OR lower(host) LIKE '%.openai.com'
                 )
                   AND (expiry = 0 OR expiry >= ?)
            """, (now,)))

            print(
                f"         snapshot={method}; cookies={total}; "
                f"chatgpt/openai={all_match}; valid={len(rows)}",
                flush=True,
            )

            if not rows:
                continue

            groups = {}
            for row in rows:
                oa = row[8] or ""
                groups.setdefault(oa, []).append(row)

            def group_score(item):
                oa, rr = item
                names = [(r[2] or "").lower() for r in rr]
                authish = sum(
                    any(k in n for k in (
                        "session", "auth", "token", "clearance",
                        "puid", "account", "refresh"
                    ))
                    for n in names
                )
                chatgpt = sum("chatgpt.com" in (r[0] or "").lower() for r in rr)
                last = max((int(r[7] or 0) for r in rr), default=0)
                return (authish > 0, authish, chatgpt, last, len(rr))

            oa, chosen = max(groups.items(), key=group_score)
            results.append({
                "db": db,
                "source": source,
                "method": method,
                "origin": oa,
                "rows": chosen,
                "score": group_score((oa, chosen)),
            })
        finally:
            con.close()
    except Exception as exc:
        print(f"         SKIP: {type(exc).__name__}", flush=True)

if not results:
    print("", flush=True)
    print("ERROR: профили прочитаны, но действующих chatgpt.com/openai.com cookies нет.", flush=True)
    print("Диагностика выше не содержит значений cookies.", flush=True)
    raise SystemExit(3)

best = max(results, key=lambda x: x["score"])

dedup = {}
for r in best["rows"]:
    host, path, name, value, expiry, secure, httponly, accessed, oa = r
    key = (host, path or "/", name)
    old = dedup.get(key)
    if old is None or int(accessed or 0) > int(old[7] or 0):
        dedup[key] = r

lines = [
    "# Netscape HTTP Cookie File",
    "# Temporary local Firefox session snapshot.",
]
written = 0
chatgpt_written = 0
for r in dedup.values():
    host, path, name, value, expiry, secure, httponly, accessed, oa = r
    if not host or not name:
        continue
    domain = str(host)
    include_sub = "TRUE" if domain.startswith(".") else "FALSE"
    if int(httponly or 0):
        domain = "#HttpOnly_" + domain
    fields = [
        domain,
        include_sub,
        str(path or "/").replace("\t", ""),
        "TRUE" if int(secure or 0) else "FALSE",
        str(int(expiry or 0)),
        str(name).replace("\t", "").replace("\r", "").replace("\n", ""),
        str(value or "").replace("\t", "").replace("\r", "").replace("\n", ""),
    ]
    lines.append("\t".join(fields))
    written += 1
    if "chatgpt.com" in str(host).lower():
        chatgpt_written += 1

jar_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
os.chmod(jar_path, 0o600)

meta_path.write_text(
    f"profile={best['db'].parent}\n"
    f"source={best['source']}\n"
    f"snapshot_method={best['method']}\n"
    f"cookies={written}\n"
    f"chatgpt_cookies={chatgpt_written}\n"
    f"origin_partitioned={'yes' if best['origin'] else 'no'}\n",
    encoding="utf-8",
)
os.chmod(meta_path, 0o600)
PY
COOKIE_RC=$?

if [ "$COOKIE_RC" -ne 0 ]; then
    echo
    echo "STOP: Firefox cookie discovery rc=$COOKIE_RC."
    echo "Export partial не изменён."
    exit "$COOKIE_RC"
fi

PROFILE="$(grep '^profile=' "$COOKIE_META" | cut -d= -f2-)"
SOURCE="$(grep '^source=' "$COOKIE_META" | cut -d= -f2-)"
METHOD="$(grep '^snapshot_method=' "$COOKIE_META" | cut -d= -f2-)"
COUNT="$(grep '^cookies=' "$COOKIE_META" | cut -d= -f2-)"
CGCOUNT="$(grep '^chatgpt_cookies=' "$COOKIE_META" | cut -d= -f2-)"
PARTITIONED="$(grep '^origin_partitioned=' "$COOKIE_META" | cut -d= -f2-)"

echo
echo "  SELECTED profile: $PROFILE"
echo "  Discovery: $SOURCE"
echo "  Snapshot: $METHOD"
echo "  Selected cookies: $COUNT; chatgpt.com=$CGCOUNT"
echo "  Firefox container/partition: $PARTITIONED"
echo "  Cookie values скрыты и удалятся из temp автоматически."
echo

if [ -f "$FINAL" ]; then
    if zip_test "$FINAL"; then
        echo "PASS: итоговый ZIP уже валиден."
        sha256sum "$FINAL"
        exit 0
    fi

    if [ -f "$PART" ]; then
        SAVED="$FINAL.invalid.$STAMP"
        mv -- "$FINAL" "$SAVED"
        echo "Дополнительный невалидный FINAL сохранён: $SAVED"
    else
        mv -- "$FINAL" "$PART"
        echo "Невалидный FINAL переведён в resume-файл: $PART"
    fi
fi

if [ -f "$PART" ]; then
    START="$(stat -c '%s' "$PART")" || die "не удалось определить размер partial"
else
    START=0
fi

echo "2/4 Local export state:"
echo "  partial=$PART"
echo "  local_size=$START bytes"
echo

CURL_COMMON=(
    --location
    --fail
    --show-error
    --connect-timeout 30
    --speed-time 120
    --speed-limit 1024
    --cookie "$COOKIEJAR"
    --cookie-jar "$COOKIEJAR"
    --user-agent "$UA"
    --header "Referer: https://chatgpt.com/"
    --header "Accept: */*"
    --header "Accept-Encoding: identity"
)

# Universal segmented assembler with adaptive range sizing.
# Existing PART is the durable checkpoint. A downloaded segment is appended only
# after exact 206/Content-Range/body-length validation.
echo "3/4 Segmented authenticated Range download"
echo "  base_chunk=$CHUNK_BYTES bytes (128 MiB)"
echo "  min_chunk=$MIN_CHUNK_BYTES bytes (16 MiB)"
echo "  checkpoint_start=$START bytes"
echo "  bulk segment path: $CHUNK"
echo

POS="$START"
TOTAL=""
SEGMENT_NO=0

if [ "$POS" -eq 0 ] && [ ! -f "$PART" ]; then
    : > "$PART" || die "не удалось создать $PART"
fi

while :; do
    if [ -n "$TOTAL" ] && [ "$POS" -ge "$TOTAL" ]; then
        break
    fi

    SEGMENT_NO=$((SEGMENT_NO + 1))
    CURRENT_CHUNK="$CHUNK_BYTES"
    ATTEMPT=0

    while :; do
        ATTEMPT=$((ATTEMPT + 1))
        REQ_END=$((POS + CURRENT_CHUNK - 1))
        if [ -n "$TOTAL" ] && [ "$REQ_END" -ge "$TOTAL" ]; then
            REQ_END=$((TOTAL - 1))
        fi

        echo "  segment #$SEGMENT_NO attempt #$ATTEMPT: bytes=${POS}-${REQ_END} chunk=$CURRENT_CHUNK"

        rm -f -- "$CHUNK"
        : > "$HEADERS"
        umask 077

        curl "${CURL_COMMON[@]}" \
            --range "${POS}-${REQ_END}" \
            --dump-header "$HEADERS" \
            --output "$CHUNK" \
            "$URL"
        RC=$?

        STATUS="$(awk '/^HTTP\// {gsub("\r","",$2); code=$2} END{print code}' "$HEADERS")"
        CRANGE="$(grep -i '^content-range:' "$HEADERS" | tail -n1 | tr -d '\r' | sed -E 's/^[Cc]ontent-[Rr]ange:[[:space:]]*//')"

        echo "    curl_rc=$RC http=${STATUS:-UNKNOWN}"
        echo "    content_range=${CRANGE:-MISSING}"

        if [ "$RC" -ne 0 ]; then
            rm -f -- "$CHUNK"
            if [ "$STATUS" = "401" ] || [ "$STATUS" = "403" ]; then
                echo "AUTH/SIGNED-URL FAIL: Firefox cookies были переданы, HTTP $STATUS."
                echo "Resume position: $POS"
                exit 22
            fi

            if [ "$CURRENT_CHUNK" -gt "$MIN_CHUNK_BYTES" ]; then
                NEXT=$((CURRENT_CHUNK / 2))
                if [ "$NEXT" -lt "$MIN_CHUNK_BYTES" ]; then NEXT="$MIN_CHUNK_BYTES"; fi
                echo "    transport failure; retry same offset with smaller chunk: $NEXT bytes"
                CURRENT_CHUNK="$NEXT"
                sleep 2
                continue
            fi

            echo "STOP: transport failure at minimum chunk size."
            echo "Resume position: $POS"
            exit "$RC"
        fi

        if [ "$STATUS" != "206" ]; then
            rm -f -- "$CHUNK"
            die "сервер не вернул 206 Partial Content; checkpoint не изменён"
        fi

        if [[ "$CRANGE" =~ ^bytes[[:space:]]+([0-9]+)-([0-9]+)/([0-9]+)$ ]]; then
            REMOTE_START="${BASH_REMATCH[1]}"
            REMOTE_END="${BASH_REMATCH[2]}"
            REMOTE_TOTAL="${BASH_REMATCH[3]}"
        else
            rm -f -- "$CHUNK"
            die "неожиданный Content-Range; checkpoint не изменён"
        fi

        if [ "$REMOTE_START" -ne "$POS" ]; then
            rm -f -- "$CHUNK"
            die "Content-Range start=$REMOTE_START, ожидался $POS"
        fi
        if [ "$REMOTE_END" -gt "$REQ_END" ]; then
            rm -f -- "$CHUNK"
            die "server range end=$REMOTE_END > requested end=$REQ_END"
        fi
        if [ "$REMOTE_END" -lt "$REMOTE_START" ]; then
            rm -f -- "$CHUNK"
            die "некорректный Content-Range: end < start"
        fi

        ACTUAL="$(stat -c '%s' "$CHUNK" 2>/dev/null)" || {
            rm -f -- "$CHUNK"
            die "не удалось определить размер segment"
        }
        EXPECTED=$((REMOTE_END - REMOTE_START + 1))

        if [ "$ACTUAL" -ne "$EXPECTED" ]; then
            echo "    SHORT BODY: downloaded=$ACTUAL expected=$EXPECTED"
            rm -f -- "$CHUNK"

            if [ "$CURRENT_CHUNK" -gt "$MIN_CHUNK_BYTES" ]; then
                NEXT=$((CURRENT_CHUNK / 2))
                if [ "$NEXT" -lt "$MIN_CHUNK_BYTES" ]; then NEXT="$MIN_CHUNK_BYTES"; fi
                echo "    checkpoint unchanged; retry same offset with $NEXT-byte range"
                CURRENT_CHUNK="$NEXT"
                sleep 2
                continue
            fi

            echo "STOP: short body persists at minimum chunk size."
            echo "Resume position: $POS"
            exit 24
        fi

        # Segment is now internally consistent. Only now accept/compare TOTAL.
        if [ -z "$TOTAL" ]; then
            TOTAL="$REMOTE_TOTAL"
            echo "    remote_total=$TOTAL bytes"

            FREE="$(df -PB1 "$DIR" 2>/dev/null | awk 'NR==2 {print $4}')"
            NEED=$((TOTAL - POS))
            MARGIN=$((1024 * 1024 * 1024))
            if [ -n "$FREE" ] && [ "$FREE" -lt $((NEED + MARGIN)) ]; then
                rm -f -- "$CHUNK"
                echo "STOP: недостаточно свободного места."
                echo "  free=$FREE still_needed=$NEED safety_margin=$MARGIN"
                echo "PART сохранён: $PART"
                exit 28
            fi
        elif [ "$REMOTE_TOTAL" -ne "$TOTAL" ]; then
            rm -f -- "$CHUNK"
            die "remote total изменился: было $TOTAL, стало $REMOTE_TOTAL"
        fi

        BEFORE="$(stat -c '%s' "$PART")"
        if [ "$BEFORE" -ne "$POS" ]; then
            rm -f -- "$CHUNK"
            die "локальный PART изменился: size=$BEFORE expected=$POS"
        fi

        echo "    validation=PASS; downloaded=$ACTUAL; appending..."
        cat -- "$CHUNK" >> "$PART"
        CAT_RC=$?

        if [ "$CAT_RC" -ne 0 ]; then
            truncate -s "$BEFORE" "$PART" 2>/dev/null || true
            rm -f -- "$CHUNK"
            die "append не завершён; rollback до $BEFORE"
        fi

        AFTER="$(stat -c '%s' "$PART")"
        EXPECTED_AFTER=$((REMOTE_END + 1))
        if [ "$AFTER" -ne "$EXPECTED_AFTER" ]; then
            truncate -s "$BEFORE" "$PART" 2>/dev/null || true
            rm -f -- "$CHUNK"
            die "after=$AFTER expected=$EXPECTED_AFTER; rollback выполнен"
        fi

        sync -f "$PART" 2>/dev/null || sync 2>/dev/null || true
        rm -f -- "$CHUNK"
        POS="$AFTER"

        echo "    assembled=$POS / $TOTAL bytes"
        echo
        break
    done
done

rm -f -- "$CHUNK"

echo "  transfer complete: assembled=$POS bytes"
[ -n "$TOTAL" ] || die "remote total не определён"
[ "$POS" -eq "$TOTAL" ] || die "assembled=$POS != remote total=$TOTAL"

echo
echo "4/4 ZIP integrity test..."
if zip_test "$PART"; then
    mv -- "$PART" "$FINAL"
    echo
    echo "PASS: архив собран полностью и ZIP валиден."
    stat -c 'size=%s bytes' "$FINAL"
    sha256sum "$FINAL"
    echo "FINAL=$FINAL"
    exit 0
fi

echo
echo "ZIP TEST: FAIL."
echo "Все HTTP segments были проверены, но итоговый ZIP невалиден."
echo "Собранный файл НЕ удалён:"
echo "  $PART"
echo "Автоматический полный redownload ~remote_total bytes НЕ запускается."
exit 4
