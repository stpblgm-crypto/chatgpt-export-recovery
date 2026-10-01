#!/usr/bin/env python3
"""EXPERIMENTAL / NOT LIVE VERIFIED: plaintext Chromium cookie adapter.

Use only where explicit runtime policy permits reading the selected database.
No custom cryptography or CDP. The optional pinned browser-cookie3 adapter must
be explicitly selected and separately authorized for the current runtime.
"""
import contextlib
import importlib.metadata
import io
import os
import signal
import tempfile
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time

CHROMIUM_EPOCH_SECONDS = 11644473600


class ProviderError(Exception):
    """Only fixed, non-secret diagnostic messages may be used."""


def candidates():
    override = os.environ.get("CHATGPT_RECOVERY_CHROMIUM_COOKIE_DB", "")
    profile = os.environ.get("CHATGPT_RECOVERY_CHROMIUM_PROFILE", "")
    if override and profile:
        raise ProviderError("choose a profile directory or a database override, not both")
    if profile:
        root = Path(profile).expanduser()
        for relative in ("Network/Cookies", "Cookies"):
            path = root / relative
            if path.is_file():
                return [path]
        return []
    if override:
        path = Path(override).expanduser()
        return [path] if path.is_file() else []
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    roots = [
        config / "chromium", config / "google-chrome",
        home / "snap/chromium/common/chromium",
        home / ".var/app/org.chromium.Chromium/config/chromium",
        home / ".var/app/com.google.Chrome/config/google-chrome",
    ]
    found = []
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        for profile in sorted(root.iterdir()):
            if profile.name != "Default" and not re.fullmatch(r"Profile [0-9]+", profile.name):
                continue
            # Modern Network/Cookies takes precedence over the legacy location.
            for relative in ("Network/Cookies", "Cookies"):
                database = profile / relative
                if database.is_file():
                    key = database.resolve()
                    if key not in seen:
                        seen.add(key)
                        found.append(database)
                    break
    return found


def allowed_domain(host):
    if not isinstance(host, str):
        return False
    domain = host.lower().lstrip(".")
    return any(domain == base or domain.endswith("." + base)
               for base in ("chatgpt.com", "openai.com"))


def safe_field(value):
    return isinstance(value, str) and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def private_write(path, text):
    # O_EXCL refuses both existing outputs and symlinks. The runtime dir is private.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)


def cookie_rows(database):
    # URL-escape the filename: '?' and '#' in paths must never alter URI options.
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cookies)")}
        required = {"host_key", "path", "name", "value", "encrypted_value", "expires_utc", "is_secure", "is_httponly"}
        if not required.issubset(cols):
            raise ProviderError("unsupported Chromium cookie schema")
        optional = [name if name in cols else fallback for name, fallback in (
            ("is_persistent", "1"), ("top_frame_site_key", "''"),
            ("has_cross_site_ancestor", "0"), ("last_access_utc", "0"),
        )]
        # Avoid loading unrelated domains' values into this process.
        query = """SELECT host_key, path, name, value, encrypted_value,
                   expires_utc, is_secure, is_httponly, """ + ", ".join(optional) + """
                   FROM cookies WHERE lower(host_key) IN ('chatgpt.com', 'openai.com')
                   OR lower(host_key) LIKE '%.chatgpt.com'
                   OR lower(host_key) LIKE '%.openai.com'"""
        rows = conn.execute(query).fetchall()
        version = 0
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
            version_row = conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()
            if version_row:
                version = int(version_row[0])
        return rows, version


def library_values(rows, version, work):
    """Only the maintained, explicitly selected dependency performs decryption."""
    try:
        if importlib.metadata.version("browser-cookie3") != "0.20.1":
            raise ProviderError("optional browser-cookie3 must be exactly version 0.20.1")
        import browser_cookie3
    except ImportError:
        raise ProviderError("optional browser-cookie3 0.20.1 is not installed in this Python environment") from None
    except importlib.metadata.PackageNotFoundError:
        raise ProviderError("optional browser-cookie3 0.20.1 is not installed in this Python environment") from None

    product = os.environ.get("CHATGPT_RECOVERY_CHROMIUM_PRODUCT", "chromium")
    if product not in ("chromium", "chrome"):
        raise ProviderError("Chromium product must be chromium or chrome")
    def timeout(_signum, _frame):
        raise ProviderError("optional cookie adapter timed out")
    previous_handler = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(20)
    try:
        # Never give the dependency a broad live DB: its substring filter is not
        # sufficient domain isolation and it does not preserve cookie partitions.
        with tempfile.TemporaryDirectory(prefix="chromium-filtered.", dir=work) as directory:
            snapshot = Path(directory) / "Cookies"
            fd = os.open(snapshot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with sqlite3.connect(snapshot) as conn:
                conn.execute("CREATE TABLE cookies (host_key TEXT, path TEXT, name TEXT, value TEXT, encrypted_value BLOB, expires_utc INTEGER, is_secure INTEGER, is_httponly INTEGER)")
                conn.executemany("INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?)", [row[:8] for row in rows])
                conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
                conn.execute("INSERT INTO meta VALUES ('version', ?)", (str(version),))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                jar = getattr(browser_cookie3, product)(cookie_file=str(snapshot), domain_name="")
            expected = {(row[0].lower(), row[1] or "/", row[2]) for row in rows}
            values = {}
            for cookie in jar:
                key = (cookie.domain.lower(), cookie.path, cookie.name)
                if key not in expected or not allowed_domain(cookie.domain) or not safe_field(cookie.value):
                    raise ProviderError("optional cookie adapter returned unexpected cookie data")
                values[key] = cookie.value
            if values.keys() != expected:
                raise ProviderError("optional cookie adapter did not return every selected cookie")
            return values
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def build(jar, meta, work):
    work = work.resolve()
    info = work.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ProviderError("runtime directory must be owned by this user and mode 0700")
    if jar.parent.resolve() != work or meta.parent.resolve() != work or jar == meta:
        raise ProviderError("cookie outputs must be distinct files inside the private runtime directory")
    available = candidates()
    if not available:
        raise ProviderError("no Chromium cookie database found")
    if len(available) != 1:
        raise ProviderError("multiple Chromium profiles found; select one using CHATGPT_RECOVERY_CHROMIUM_COOKIE_DB")
    backend = os.environ.get("CHATGPT_RECOVERY_CHROMIUM_BACKEND", "plaintext")
    if backend not in ("plaintext", "browser-cookie3"):
        raise ProviderError("Chromium cookie backend must be plaintext or browser-cookie3")
    partition_selection = os.environ.get("CHATGPT_RECOVERY_CHROMIUM_COOKIE_PARTITION", "reject")
    if partition_selection not in ("reject", "unpartitioned"):
        raise ProviderError("Chromium cookie partition must be reject or unpartitioned")
    selected = {}
    omitted_partitioned = 0
    now = int(time.time())
    rows, version = cookie_rows(available[0])
    live_rows = []
    for row in rows:
        host, path, name, value, encrypted, expires, secure, http_only, persistent, partition, cross_site, accessed = row
        if not allowed_domain(host):
            continue
        # Explicit narrowing never flattens a partition into curl's global jar.
        # Skip before encryption inspection or any optional dependency handling.
        if partition and partition_selection == "unpartitioned":
            omitted_partitioned += 1
            continue
        expiry = int(expires or 0) // 1000000 - CHROMIUM_EPOCH_SECONDS if expires else 0
        if not persistent:
            expiry = 0
        if expiry and expiry <= now:
            continue
        # Do not silently drop encrypted auth or flatten partitioned cookies.
        if encrypted and backend == "plaintext":
            raise ProviderError("encrypted Chromium cookies are unsupported in plaintext mode; no decryption or keyring access was attempted")
        if encrypted and (not isinstance(encrypted, bytes) or encrypted[:3] not in (b"v10", b"v11")):
            raise ProviderError("unsupported Chromium encryption scheme; optional adapter was not called")
        # The ancestor bit can be true for an unpartitioned cookie; the
        # serialized top-frame site determines whether a partition exists.
        if partition:
            raise ProviderError("partitioned Chromium cookies are unsupported; no unpartitioned jar was created")
        path = path or "/"
        if not name or not path.startswith("/") or not all(safe_field(x) for x in (host, path, name, value)):
            raise ProviderError("cookie fields are not representable safely in a Netscape jar")
        if not persistent:
            row = row[:5] + (0,) + row[6:]
        live_rows.append(row)
        key = (host.lower(), path, name)
        previous = selected.get(key)
        if previous is None or int(accessed or 0) > previous[1]:
            domain = ("#HttpOnly_" if http_only else "") + host.lower()
            fields = [domain, "TRUE" if host.startswith(".") else "FALSE", path,
                      "TRUE" if secure else "FALSE", str(expiry), name, value]
            selected[key] = ("\t".join(fields), int(accessed or 0))
    if not selected:
        raise ProviderError("no valid plaintext ChatGPT/OpenAI cookies found")
    if backend == "browser-cookie3":
        # Deduplicate before the dependency, using the same last-access rule.
        unique = {}
        for row in live_rows:
            key = (row[0].lower(), row[1] or "/", row[2])
            if key not in unique or int(row[11] or 0) > int(unique[key][11] or 0):
                unique[key] = row
        values = library_values(list(unique.values()), version, work)
        for key, value in values.items():
            if unique[key][4] and not value:
                raise ProviderError("optional cookie adapter returned an empty encrypted cookie")
            fields = selected[key][0].split("\t")
            fields[-1] = value
            selected[key] = ("\t".join(fields), selected[key][1])
    private_write(jar, "# Netscape HTTP Cookie File\n# Experimental Chromium session snapshot.\n" +
                  "\n".join(selected[key][0] for key in sorted(selected)) + "\n")
    private_write(meta, "profile=explicit-or-single-profile\nsource=chromium-sqlite-readonly\n"
                  "snapshot_method=read-transaction\nbackend=" + backend + "\ncookies=" + str(len(selected)) +
                  "\nchatgpt_cookies=" + str(sum(k[0].lstrip(".") == "chatgpt.com" or
                                               k[0].endswith(".chatgpt.com") for k in selected)) +
                  "\norigin_partitioned=no\npartition_selection=" + partition_selection +
                  "\npartitioned_rows_omitted=" + str(omitted_partitioned) + "\n")
    print("Chromium session snapshot created; cookie values withheld.")


def main():
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "detect":
            return 0 if candidates() else 1
        if len(sys.argv) != 5 or sys.argv[1] != "build":
            raise ProviderError("invalid Chromium provider invocation")
        os.umask(0o077)
        build(*(Path(arg) for arg in sys.argv[2:]))
        return 0
    except ProviderError as error:
        print("ERROR: " + str(error) + ".", file=sys.stderr)
    except Exception:
        # SQLite and filesystem exceptions may embed paths or data; never print them.
        print("ERROR: Chromium provider could not safely read the selected database.", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
