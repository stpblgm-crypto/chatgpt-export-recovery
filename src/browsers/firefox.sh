#!/usr/bin/env bash

# Firefox session provider. Cookie values are written only to a mode-0600
# temporary jar; diagnostics contain paths, methods, and counts only.

firefox_detect_browser() {
    local firefox_root
    if command -v firefox >/dev/null 2>&1; then
        return 0
    fi
    for firefox_root in \
        "$HOME/snap/firefox/common/.mozilla/firefox" \
        "$HOME/.mozilla/firefox" \
        "$HOME/.var/app/org.mozilla.firefox/.mozilla/firefox"; do
        if [ -d "$firefox_root" ]; then
            return 0
        fi
    done
    return 1
}

firefox_user_agent() {
    local version
    version="$(firefox --version 2>/dev/null | grep -oE '[0-9]+([.][0-9]+)+' | tail -n1 || true)"
    [ -n "$version" ] || version="154.0"
    printf 'Mozilla/5.0 (X11; Linux x86_64; rv:%s) Gecko/20100101 Firefox/%s\n' \
        "$version" "$version"
}

firefox_build_temporary_cookie_jar() {
    local cookie_jar="${1-}"
    local cookie_meta="${2-}"
    local work_dir="${3-}"

    if [ -z "$cookie_jar" ] || [ -z "$cookie_meta" ] || [ ! -d "$work_dir" ]; then
        printf 'ERROR: Firefox provider received incomplete inputs.\n' >&2
        return 2
    fi
    command -v python3 >/dev/null 2>&1 || {
        printf 'ERROR: python3 is required for Firefox cookie snapshots.\n' >&2
        return 2
    }

    COOKIE_JAR_PATH="$cookie_jar" \
    COOKIE_META_PATH="$cookie_meta" \
    COOKIE_WORK_DIR="$work_dir" \
    FIREFOX_COOKIE_DB_OVERRIDE="${CHATGPT_RECOVERY_FIREFOX_COOKIE_DB:-}" \
    python3 <<'PY'
import configparser
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

home_dir = Path.home()
jar_path = Path(os.environ["COOKIE_JAR_PATH"])
meta_path = Path(os.environ["COOKIE_META_PATH"])
work_dir = Path(os.environ["COOKIE_WORK_DIR"])
override = os.environ.get("FIREFOX_COOKIE_DB_OVERRIDE", "").strip()

candidates = []
seen = set()


def add_database(path, source):
    try:
        candidate = Path(path).expanduser()
        if candidate.name != "cookies.sqlite" or not candidate.is_file():
            return
        resolved = candidate.resolve()
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        candidates.append((resolved, source))
    except Exception:
        return


if override:
    add_database(override, "explicit-override")
else:
    # Strongest signal: a database currently open by a Firefox process.
    for process_dir in Path("/proc").glob("[0-9]*"):
        try:
            command = (
                (process_dir / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", "ignore")
                .lower()
            )
        except Exception:
            continue
        if "firefox" not in command:
            continue
        try:
            descriptors = list((process_dir / "fd").iterdir())
        except Exception:
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except Exception:
                continue
            if target.endswith("/cookies.sqlite"):
                add_database(Path(target), "open-by-firefox")
            elif target.endswith("/cookies.sqlite-wal") or target.endswith("/cookies.sqlite-shm"):
                add_database(Path(target.rsplit("-", 1)[0]), "open-by-firefox")

    roots = [
        home_dir / "snap/firefox/common/.mozilla/firefox",
        home_dir / ".mozilla/firefox",
        home_dir / ".var/app/org.mozilla.firefox/.mozilla/firefox",
    ]

    # profiles.ini provides the next strongest bounded source of candidates.
    for root in roots:
        profiles_file = root / "profiles.ini"
        if not profiles_file.is_file():
            continue
        parser = configparser.RawConfigParser()
        try:
            parser.read(profiles_file, encoding="utf-8")
            for section in parser.sections():
                if not section.startswith("Profile"):
                    continue
                raw_path = parser.get(section, "Path", fallback="").strip()
                if not raw_path:
                    continue
                is_relative = parser.getboolean(section, "IsRelative", fallback=True)
                profile = root / raw_path if is_relative else Path(raw_path).expanduser()
                add_database(profile / "cookies.sqlite", "profiles.ini")
        except Exception:
            pass

    # Bounded, one-level fallback; never recursively scan the Firefox tree.
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for child in root.iterdir():
                if child.is_dir():
                    add_database(child / "cookies.sqlite", "profile-dir")
        except Exception:
            pass

if not candidates:
    print("ERROR: no Firefox cookies.sqlite candidate was found.", flush=True)
    raise SystemExit(3)

print(f"Firefox candidate databases: {len(candidates)}", flush=True)

backup_program = r'''
import sqlite3
import sys

source_path, destination_path = sys.argv[1], sys.argv[2]
source = sqlite3.connect("file:" + source_path + "?mode=ro", uri=True, timeout=1)
destination = sqlite3.connect(destination_path)
try:
    source.backup(destination, pages=256, sleep=0.05)
finally:
    destination.close()
    source.close()
'''


def snapshot_database(database, index):
    online_copy = work_dir / f"online_{index}.sqlite"
    try:
        subprocess.run(
            [sys.executable, "-c", backup_program, str(database), str(online_copy)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
            check=True,
        )
        return online_copy, "online-backup"
    except Exception:
        try:
            online_copy.unlink(missing_ok=True)
        except Exception:
            pass

    copy_dir = work_dir / f"copy_{index}"
    copy_dir.mkdir(mode=0o700, exist_ok=True)
    target = copy_dir / "cookies.sqlite"
    wal = Path(str(database) + "-wal")
    shm = Path(str(database) + "-shm")

    # Copy the WAL before and after the DB to improve the chance of a coherent
    # fallback snapshot while a live Firefox instance is writing.
    if wal.is_file():
        try:
            shutil.copy2(wal, Path(str(target) + "-wal"))
        except Exception:
            pass
    shutil.copy2(database, target)
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
    return target, "db-wal-copy"


results = []
now = int(time.time())

for index, (database, source) in enumerate(candidates, 1):
    print(f"  [{index}/{len(candidates)}] {database.parent} ({source})", flush=True)
    try:
        snapshot, method = snapshot_database(database, index)
        connection = sqlite3.connect(snapshot, timeout=2)
        try:
            total_count = int(connection.execute("SELECT COUNT(*) FROM moz_cookies").fetchone()[0])
            columns = {row[1] for row in connection.execute("PRAGMA table_info(moz_cookies)")}
            access_column = "lastAccessed" if "lastAccessed" in columns else "0"
            origin_column = "originAttributes" if "originAttributes" in columns else "''"

            matching_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM moz_cookies
                     WHERE lower(host) = 'chatgpt.com'
                        OR lower(host) LIKE '%.chatgpt.com'
                        OR lower(host) = 'openai.com'
                        OR lower(host) LIKE '%.openai.com'
                    """
                ).fetchone()[0]
            )

            rows = list(
                connection.execute(
                    f"""
                    SELECT host, path, name, value, expiry, isSecure, isHttpOnly,
                           {access_column}, {origin_column}
                      FROM moz_cookies
                     WHERE (
                            lower(host) = 'chatgpt.com'
                         OR lower(host) LIKE '%.chatgpt.com'
                         OR lower(host) = 'openai.com'
                         OR lower(host) LIKE '%.openai.com'
                     )
                       AND (expiry = 0 OR expiry >= ?)
                    """,
                    (now,),
                )
            )

            print(
                f"      snapshot={method}; cookies={total_count}; "
                f"chatgpt/openai={matching_count}; valid={len(rows)}",
                flush=True,
            )
            if not rows:
                continue

            groups = {}
            for row in rows:
                origin = row[8] or ""
                groups.setdefault(origin, []).append(row)

            def group_score(item):
                _origin, group_rows = item
                names = [(row[2] or "").lower() for row in group_rows]
                auth_like = sum(
                    any(marker in name for marker in (
                        "session", "auth", "token", "clearance", "puid", "account", "refresh"
                    ))
                    for name in names
                )
                chatgpt_count = sum(
                    "chatgpt.com" in (row[0] or "").lower() for row in group_rows
                )
                last_access = max((int(row[7] or 0) for row in group_rows), default=0)
                return (auth_like > 0, auth_like, chatgpt_count, last_access, len(group_rows))

            origin, chosen_rows = max(groups.items(), key=group_score)
            results.append(
                {
                    "database": database,
                    "source": source,
                    "method": method,
                    "origin": origin,
                    "rows": chosen_rows,
                    "score": group_score((origin, chosen_rows)),
                }
            )
        finally:
            connection.close()
    except Exception as error:
        print(f"      SKIP: {type(error).__name__}", flush=True)

if not results:
    print("ERROR: Firefox profiles were readable, but no valid ChatGPT/OpenAI cookies were found.")
    print("Diagnostics above contain no cookie values.")
    raise SystemExit(3)

best = max(results, key=lambda result: result["score"])

deduplicated = {}
for row in best["rows"]:
    host, path, name, value, expiry, secure, http_only, accessed, origin = row
    key = (host, path or "/", name)
    previous = deduplicated.get(key)
    if previous is None or int(accessed or 0) > int(previous[7] or 0):
        deduplicated[key] = row

lines = [
    "# Netscape HTTP Cookie File",
    "# Temporary local Firefox session snapshot.",
]
written = 0
chatgpt_written = 0

for row in deduplicated.values():
    host, path, name, value, expiry, secure, http_only, accessed, origin = row
    if not host or not name:
        continue
    domain = str(host)
    include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
    if int(http_only or 0):
        domain = "#HttpOnly_" + domain
    fields = [
        domain,
        include_subdomains,
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
    f"profile={best['database'].parent}\n"
    f"source={best['source']}\n"
    f"snapshot_method={best['method']}\n"
    f"cookies={written}\n"
    f"chatgpt_cookies={chatgpt_written}\n"
    f"origin_partitioned={'yes' if best['origin'] else 'no'}\n",
    encoding="utf-8",
)
os.chmod(meta_path, 0o600)
PY
}

firefox_cleanup_session_material() {
    local work_dir="${1-}"
    case "$work_dir" in
        */chatgpt-export-recovery.*)
            if [ -d "$work_dir" ]; then
                rm -rf -- "$work_dir"
            fi
            ;;
        *)
            printf 'ERROR: refusing to clean an unexpected runtime path.\n' >&2
            return 1
            ;;
    esac
}
