"""Explicit local Firefox authentication; no cookie files or profile snapshots."""

import configparser
import http.cookiejar
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

from .core import HTTPTransport, RecoveryError, _HTTPSRedirect, validate_url


def _safe_text(value):
    return isinstance(value, str) and all(32 <= ord(c) < 127 for c in value)


class _ChatGPTCookiePolicy(http.cookiejar.DefaultCookiePolicy):
    def __init__(self):
        super().__init__(strict_ns_domain=self.DomainStrict)

    def return_ok(self, cookie, request):
        try:
            target = urllib.parse.urlsplit(request.full_url)
            if target.scheme != "https" or target.hostname != "chatgpt.com" or target.port not in (None, 443):
                return False
        except (TypeError, ValueError):
            return False
        return super().return_ok(cookie, request)

    def set_ok(self, cookie, request):
        # The selected browser is the sole source. Do not persist server cookies.
        return False


def _firefox_agent(profile):
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(profile / "compatibility.ini", encoding="utf-8")
        raw = parser.get("Compatibility", "LastVersion")
        version = re.match(r"[0-9]+(?:\.[0-9]+)*", raw).group()
        return f"Mozilla/5.0 (X11; Linux x86_64; rv:{version}) Gecko/20100101 Firefox/{version}"
    except (OSError, configparser.Error, AttributeError):
        raise RecoveryError("The selected Firefox profile has no readable browser version metadata.") from None


def _load_jar(profile):
    database = profile / "cookies.sqlite"
    if database.is_symlink() or not database.is_file():
        raise RecoveryError("Select an existing Firefox profile with a regular cookies.sqlite file.")
    jar = http.cookiejar.CookieJar(policy=_ChatGPTCookiePolicy())
    connection = None
    try:
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(moz_cookies)")}
        required = {"host", "path", "name", "value", "expiry", "isSecure", "isHttpOnly", "originAttributes", "lastAccessed"}
        if not required.issubset(columns):
            raise RecoveryError("The selected Firefox cookie database schema is unsupported.")
        # Read only the first-party ChatGPT partition. Never load unrelated values
        # or flatten a Firefox container / partition into a global cookie jar.
        rows = connection.execute("""
            SELECT host, path, name, value, expiry, isSecure, isHttpOnly, lastAccessed
              FROM moz_cookies
             WHERE lower(host) IN ('chatgpt.com', '.chatgpt.com')
               AND originAttributes = '' AND (expiry = 0 OR expiry > ?)
             ORDER BY lastAccessed ASC
        """, (int(time.time()),)).fetchall()
        for host, path, name, value, expiry, secure, http_only, accessed in rows:
            if (not all(_safe_text(x) for x in (host, path, name, value))
                    or not path.startswith("/") or not name
                    or re.search(r"[\s(),/:;<=>?@\[\]{}\\\"]", name)
                    or ";" in value or not isinstance(expiry, int) or expiry < 0):
                raise RecoveryError("A selected Firefox cookie cannot be represented safely. No request was sent.")
            jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=name, value=value, port=None, port_specified=False,
                domain=host.lower(), domain_specified=host.startswith("."),
                domain_initial_dot=host.startswith("."), path=path, path_specified=True,
                secure=bool(secure), expires=expiry or None, discard=not expiry,
                comment=None, comment_url=None,
                rest={"HttpOnly": None} if http_only else {}, rfc2109=False))
        if not any("session" in cookie.name.lower() or "auth" in cookie.name.lower() for cookie in jar):
            raise RecoveryError("No unexpired first-party ChatGPT session was found in this Firefox profile. Sign in normally in Firefox.")
        return jar
    except RecoveryError:
        jar.clear()
        raise
    except Exception:
        jar.clear()
        raise RecoveryError("The Firefox session could not be read in read-only mode. No cookie snapshot or jar file was created.") from None
    finally:
        if connection is not None:
            connection.close()


class _FirefoxAgent(urllib.request.BaseHandler):
    def __init__(self, user_agent):
        self.user_agent = user_agent

    def http_request(self, request):
        request.add_header("User-Agent", self.user_agent)
        return request

    https_request = http_request


class FirefoxSessionTransport(HTTPTransport):
    """Keep one explicitly selected first-party Firefox session in memory."""

    def __init__(self, profile):
        if sys.platform != "linux":
            raise RecoveryError("Local Firefox session input currently supports Linux only.")
        profile = Path(profile)
        user_agent = _firefox_agent(profile)
        self.jar = _load_jar(profile)
        self.opener = urllib.request.build_opener(
            _HTTPSRedirect(), urllib.request.HTTPCookieProcessor(self.jar), _FirefoxAgent(user_agent))

    def open(self, url, start, end, etag, timeout):
        target = urllib.parse.urlsplit(validate_url(url))
        if target.scheme != "https" or target.hostname != "chatgpt.com":
            raise RecoveryError("Firefox session recovery requires an official chatgpt.com HTTPS link.")
        return super().open(url, start, end, etag, timeout)

    def close(self):
        self.jar.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
