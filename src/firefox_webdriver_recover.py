#!/usr/bin/env python3
"""Browser-mediated Range recovery for protected ChatGPT export endpoints.

This transport uses Firefox's own network stack through WebDriver, but keeps
checkpointing, range validation, and ZIP verification in Python. It is intended
as a fallback when direct curl receives a browser-gated HTTP 403.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import shutil
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service

CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$", re.I)
ALLOWED_HOSTS = ("chatgpt.com", "openai.com")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb", buffering=1024 * 1024) as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def validate_url(url: str, allow_test_http: bool = False) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in (("https", "http") if allow_test_http else ("https",)):
        raise ValueError("unsupported URL scheme")
    if parsed.username or parsed.password:
        raise ValueError("userinfo is not allowed")
    if allow_test_http and parsed.hostname in ("127.0.0.1", "localhost"):
        return
    host = (parsed.hostname or "").lower()
    if not any(host == base or host.endswith("." + base) for base in ALLOWED_HOSTS):
        raise ValueError("URL host is not ChatGPT/OpenAI")


def clone_profile(src: Path, dst: Path) -> None:
    ignore = shutil.ignore_patterns(
        "parent.lock", ".parentlock", "lock", "LOCK",
        "cache2", "startupCache", "shader-cache", "minidumps",
    )
    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)
    for name in ("parent.lock", ".parentlock", "lock", "LOCK"):
        try:
            (dst / name).unlink()
        except FileNotFoundError:
            pass


def make_driver(profile_dir: Path, headed: bool) -> webdriver.Firefox:
    opts = Options()
    if not headed:
        opts.add_argument("-headless")
    opts.binary_location = "/snap/firefox/current/usr/lib/firefox/firefox"
    opts.page_load_strategy = "none"
    opts.add_argument("-profile")
    opts.add_argument(str(profile_dir))
    opts.set_preference("browser.sessionstore.resume_from_crash", False)
    opts.set_preference("browser.startup.page", 0)
    opts.set_preference("browser.download.always_ask_before_handling_new_types", True)
    service = Service("/snap/bin/geckodriver", log_output=os.devnull)
    driver = webdriver.Firefox(service=service, options=opts)
    driver.set_page_load_timeout(12)
    driver.set_script_timeout(90)
    return driver


FETCH_SCRIPT = r"""
const url=arguments[0], start=arguments[1], end=arguments[2], timeoutMs=arguments[3], done=arguments[4];
const ctl=new AbortController();
const timer=setTimeout(()=>ctl.abort(), timeoutMs);
fetch(url, {
  method:'GET',
  credentials:'include',
  cache:'no-store',
  redirect:'follow',
  headers:{'Range':'bytes='+start+'-'+end, 'Accept':'*/*', 'Accept-Encoding':'identity'},
  signal:ctl.signal
}).then(async r=>{
  const ab=await r.arrayBuffer();
  const u=new Uint8Array(ab);
  let binary='';
  const step=0x8000;
  for(let i=0;i<u.length;i+=step) binary+=String.fromCharCode.apply(null,u.subarray(i,Math.min(i+step,u.length)));
  clearTimeout(timer);
  done({status:r.status, contentRange:r.headers.get('content-range'), bodyB64:btoa(binary), bodyBytes:u.length});
}).catch(e=>{
  clearTimeout(timer);
  done({error:(e && e.name) ? e.name : 'fetch-error'});
});
"""


def fetch_range(driver, url: str, start: int, end: int, timeout_ms: int):
    result = driver.execute_async_script(FETCH_SCRIPT, url, start, end, timeout_ms)
    if not isinstance(result, dict):
        raise RuntimeError("unexpected WebDriver result")
    if result.get("error"):
        raise RuntimeError("browser fetch failed: " + str(result["error"]))
    status = int(result.get("status", 0))
    cr = result.get("contentRange")
    body = base64.b64decode(result.get("bodyB64", ""), validate=True)
    if int(result.get("bodyBytes", -1)) != len(body):
        raise RuntimeError("browser body length mismatch")
    return status, cr, body


def parse_content_range(value: str | None, expected_start: int, requested_end: int, known_total: int | None):
    if not value:
        raise RuntimeError("missing Content-Range")
    m = CONTENT_RANGE_RE.fullmatch(value.strip())
    if not m:
        raise RuntimeError("malformed Content-Range")
    start, end, total = map(int, m.groups())
    if start != expected_start:
        raise RuntimeError("range start mismatch")
    if end < start or end > requested_end or total <= end:
        raise RuntimeError("invalid range bounds")
    if known_total is not None and total != known_total:
        raise RuntimeError("remote total changed")
    expected_end = min(requested_end, total - 1)
    if end != expected_end:
        raise RuntimeError("range end mismatch")
    return start, end, total


def append_durable(path: Path, data: bytes, expected_before: int) -> None:
    current = path.stat().st_size if path.exists() else 0
    if current != expected_before:
        raise RuntimeError("checkpoint changed concurrently")
    with path.open("ab", buffering=0) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if path.stat().st_size != expected_before + len(data):
        raise RuntimeError("checkpoint append size mismatch")


def zip_valid(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return z.testzip() is None
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url-file", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--checkpoint-file")
    ap.add_argument("--expected-size", type=int)
    ap.add_argument("--expected-sha256")
    ap.add_argument("--chunk-size", type=int, default=4 * 1024 * 1024)
    ap.add_argument("--min-chunk-size", type=int, default=1024 * 1024)
    ap.add_argument("--fetch-timeout-ms", type=int, default=60000)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--allow-test-http", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    url = Path(args.url_file).read_text(encoding="utf-8").splitlines()[0].strip()
    validate_url(url, args.allow_test_http)

    final = Path(args.output).expanduser().resolve()
    checkpoint = Path(args.checkpoint_file).expanduser().resolve() if args.checkpoint_file else Path(str(final) + ".part")
    profile = Path(args.profile).expanduser().resolve()

    if args.chunk_size < args.min_chunk_size or args.min_chunk_size <= 0:
        raise SystemExit("invalid chunk size")
    if args.checkpoint_file:
        if args.expected_size is None or not args.expected_sha256:
            raise SystemExit("explicit checkpoint requires expected size and SHA256")
        if not checkpoint.is_file() or checkpoint.stat().st_size != args.expected_size:
            raise SystemExit("checkpoint size guard failed")
        actual = sha256_file(checkpoint)
        if actual != args.expected_sha256.lower():
            raise SystemExit("checkpoint SHA256 guard failed")
        print("CHECKPOINT_GUARD=PASS", flush=True)
    elif not checkpoint.exists():
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.touch()

    if final.is_file() and final != checkpoint:
        if zip_valid(final):
            print("FINAL_ALREADY_VALID=PASS", flush=True)
            print("FINAL_SIZE=" + str(final.stat().st_size), flush=True)
            print("FINAL_SHA256=" + sha256_file(final), flush=True)
            return 0
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        saved = final.with_name(final.name + ".invalid." + stamp)
        final.rename(saved)
        print("INVALID_FINAL_PRESERVED=" + str(saved), flush=True)

    start = checkpoint.stat().st_size
    total = None
    chunk = args.chunk_size

    with tempfile.TemporaryDirectory(prefix="ff-webdriver-recovery.") as td:
        cloned = Path(td) / "profile"
        clone_profile(profile, cloned)
        driver = make_driver(cloned, args.headed)
        try:
            try:
                driver.get("https://chatgpt.com/")
            except TimeoutException:
                pass
            time.sleep(2)

            pos = start
            segment_no = 0
            while total is None or pos < total:
                segment_no += 1
                current = chunk
                while True:
                    end = pos + current - 1
                    if total is not None:
                        end = min(end, total - 1)
                    print(f"SEGMENT={segment_no} RANGE={pos}-{end}", flush=True)
                    try:
                        status, cr, body = fetch_range(driver, url, pos, end, args.fetch_timeout_ms)
                    except (RuntimeError, WebDriverException) as exc:
                        if current > args.min_chunk_size:
                            current = max(args.min_chunk_size, current // 2)
                            print(f"RETRY_SMALLER={current}", flush=True)
                            continue
                        print("BROWSER_FETCH_FAIL=" + type(exc).__name__, flush=True)
                        return 23

                    print(f"HTTP_STATUS={status}", flush=True)
                    if status in (401, 403):
                        print("AUTH_OR_SIGNED_URL_HOLD", flush=True)
                        return 22
                    if status != 206:
                        print("RANGE_STATUS_HOLD", flush=True)
                        return 24

                    try:
                        rstart, rend, rtotal = parse_content_range(cr, pos, end, total)
                    except RuntimeError:
                        print("RANGE_VALIDATION_HOLD", flush=True)
                        return 24
                    if len(body) != rend - rstart + 1:
                        if current > args.min_chunk_size:
                            current = max(args.min_chunk_size, current // 2)
                            print(f"BODY_RETRY_SMALLER={current}", flush=True)
                            continue
                        print("BODY_LENGTH_HOLD", flush=True)
                        return 24

                    if total is None:
                        total = rtotal
                        free = shutil.disk_usage(checkpoint.parent).free
                        need = total - pos
                        margin = 1024 * 1024 * 1024
                        print(f"REMOTE_TOTAL={total}", flush=True)
                        if free < need + margin:
                            print(f"DISK_HOLD free={free} need={need} margin={margin}", flush=True)
                            return 28

                    append_durable(checkpoint, body, pos)
                    pos = rend + 1
                    print(f"CHECKPOINT={pos}/{total}", flush=True)
                    break

            if total is None or checkpoint.stat().st_size != total:
                print("FINAL_SIZE_HOLD", flush=True)
                return 4
            print("ZIP_VERIFY=START", flush=True)
            if not zip_valid(checkpoint):
                print("ZIP_INTEGRITY=FAIL", flush=True)
                return 4
            if checkpoint != final:
                checkpoint.rename(final)
            print("ZIP_INTEGRITY=PASS", flush=True)
            print("FINAL_SIZE=" + str(final.stat().st_size), flush=True)
            print("FINAL_SHA256=" + sha256_file(final), flush=True)
            print("FINAL=" + str(final), flush=True)
            return 0
        finally:
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
