#!/usr/bin/env python3
import argparse
import os
import sqlite3
import tempfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service


def snapshot_cookie_db(src: Path, dst: Path) -> None:
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=2)
    target = sqlite3.connect(dst)
    try:
        source.backup(target, pages=256, sleep=0.05)
    finally:
        target.close()
        source.close()


def load_chatgpt_cookies(db: Path):
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            """SELECT name,value,host,path,expiry,isSecure,isHttpOnly,sameSite
               FROM moz_cookies
               WHERE lower(host)='chatgpt.com'
                  OR lower(host) LIKE '%.chatgpt.com'
                  OR lower(host)='openai.com'
                  OR lower(host) LIKE '%.openai.com'"""
        ).fetchall()
    finally:
        con.close()
    return rows


def add_cookie(driver, row):
    name, value, host, path, expiry, secure, httponly, samesite = row
    domain = host.lstrip(".")
    if not (domain == "chatgpt.com" or domain.endswith(".chatgpt.com")):
        return False
    cookie = {
        "name": name,
        "value": value,
        "path": path or "/",
        "secure": bool(secure),
        "httpOnly": bool(httponly),
    }
    if host.startswith("."):
        cookie["domain"] = host
    if expiry:
        cookie["expiry"] = int(expiry)
    if samesite == 1:
        cookie["sameSite"] = "Lax"
    elif samesite == 2:
        cookie["sameSite"] = "Strict"
    try:
        driver.add_cookie(cookie)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url-file", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--bytes", type=int, default=1048576)
    args = ap.parse_args()

    url = Path(args.url_file).read_text(encoding="utf-8").splitlines()[0].strip()
    end = args.start + args.bytes - 1

    with tempfile.TemporaryDirectory(prefix="ff-range-probe.") as td:
        snap = Path(td) / "cookies.sqlite"
        snapshot_cookie_db(Path(args.profile) / "cookies.sqlite", snap)
        rows = load_chatgpt_cookies(snap)

        opts = Options()
        opts.add_argument("-headless")
        opts.binary_location = "/snap/bin/firefox"
        service = Service("/snap/bin/geckodriver")
        driver = webdriver.Firefox(service=service, options=opts)
        try:
            driver.set_script_timeout(90)
            driver.get("https://chatgpt.com/")
            added = sum(1 for row in rows if add_cookie(driver, row))
            driver.get("https://chatgpt.com/")
            script = """
const url = arguments[0], start = arguments[1], end = arguments[2], done = arguments[3];
fetch(url, {
  credentials: 'include',
  headers: {'Range': 'bytes=' + start + '-' + end, 'Accept-Encoding': 'identity'}
}).then(async r => {
  const buf = await r.arrayBuffer();
  done({status:r.status, contentRange:r.headers.get('content-range'), bytes:buf.byteLength});
}).catch(e => done({error:e && e.name ? e.name : 'fetch-error'}));
"""
            result = driver.execute_async_script(script, url, args.start, end)
            print(f"COOKIES_ADDED={added}")
            if "error" in result:
                print(f"FETCH_ERROR={result['error']}")
                raise SystemExit(3)
            print(f"HTTP_STATUS={result.get('status')}")
            print(f"CONTENT_RANGE={result.get('contentRange')}")
            print(f"BODY_BYTES={result.get('bytes')}")
            ok = result.get("status") == 206 and result.get("bytes") == args.bytes
            raise SystemExit(0 if ok else 4)
        finally:
            driver.quit()


if __name__ == "__main__":
    main()
