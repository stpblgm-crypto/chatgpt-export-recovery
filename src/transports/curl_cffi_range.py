#!/usr/bin/env python3
import argparse
import http.cookiejar
import os
import re
import sys

try:
    from curl_cffi import requests
except Exception:
    print("TRANSPORT_ERROR=DEPENDENCY_UNAVAILABLE", file=sys.stderr)
    raise SystemExit(3)

RANGE_RE = re.compile(r"^(0|[1-9][0-9]{0,17})-(0|[1-9][0-9]{0,17})$")
CONTENT_RANGE_RE = re.compile(r"^bytes [0-9]+-[0-9]+/[0-9]+$")

def fail(code, name):
    print("TRANSPORT_ERROR=" + name, file=sys.stderr)
    raise SystemExit(code)

def read_url_config(path):
    text = open(path, "r", encoding="utf-8").read().strip()
    prefix = 'url = "'
    if not text.startswith(prefix) or not text.endswith('"'):
        fail(2, "URL_CONFIG_INVALID")
    return text[len(prefix):-1]
def load_session(cookie_path, impersonate):
    jar = http.cookiejar.MozillaCookieJar(cookie_path)
    jar.load(ignore_discard=True, ignore_expires=True)
    session = requests.Session(impersonate=impersonate)
    for cookie in jar:
        session.cookies.set(
            cookie.name, cookie.value,
            domain=cookie.domain, path=cookie.path or "/"
        )
    return session

def write_meta(path, status, content_range, body_bytes):
    safe_range = content_range if CONTENT_RANGE_RE.fullmatch(content_range or "") else "INVALID"
    with open(path, "w", encoding="ascii") as stream:
        stream.write(f"http={status}\n")
        stream.write(f"content_range={safe_range}\n")
        stream.write(f"body_bytes={body_bytes}\n")

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--url-config", required=True)
    parser.add_argument("--cookie-jar", required=True)
    parser.add_argument("--range", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--meta", required=True)
    parser.add_argument("--impersonate", default="chrome")
    args = parser.parse_args()
    match = RANGE_RE.fullmatch(args.range)
    if not match:
        fail(2, "RANGE_INVALID")
    start, end = map(int, match.groups())
    if end < start:
        fail(2, "RANGE_INVALID")
    maximum = end - start + 1
    url = read_url_config(args.url_config)

    try:
        session = load_session(args.cookie_jar, args.impersonate)
        response = session.get(
            url,
            headers={
                "Range": f"bytes={start}-{end}",
                "Referer": "https://chatgpt.com/",
                "Accept": "*/*",
                "Accept-Encoding": "identity",
            },
            allow_redirects=True,
            timeout=300,
            stream=True,
        )
    except Exception:
        fail(1, "REQUEST_FAILED")

    status = int(response.status_code)
    content_range = response.headers.get("content-range", "")
    written = 0
    if status == 206:
        try:
            with open(args.output, "wb") as stream:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > maximum:
                        stream.close()
                        os.unlink(args.output)
                        write_meta(args.meta, status, content_range, written)
                        fail(1, "BODY_LIMIT_EXCEEDED")
                    stream.write(chunk)
        except Exception:
            try:
                os.unlink(args.output)
            except OSError:
                pass
            fail(1, "BODY_WRITE_FAILED")

    write_meta(args.meta, status, content_range, written)
    if status in (401, 403) or 400 <= status <= 499:
        return 22
    if status >= 500:
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
