"""Mock-only discovery boundary tests: no sockets, DNS, browser or URL files."""
import contextlib
from email.message import Message
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import urllib.response


HELPER = Path(__file__).resolve().parents[1] / "src/browsers/chromium_cdp_fetch.py"
SPEC = importlib.util.spec_from_file_location("cdp_discovery_subject", HELPER)
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)
REAL_BUILD_OPENER = urllib.request.build_opener
TARGET = {
    "type": "page",
    "url": "https://chatgpt.com/",
    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/mock",
}


class MemoryHTTPHandler(urllib.request.HTTPHandler):
    """Exercise real urllib request/redirect processing without HTTP connections."""
    def __init__(self, code=200, location=None, payload=None):
        super().__init__()
        self.code = code
        self.location = location
        self.payload = [TARGET] if payload is None else payload
        self.requests = []

    def http_open(self, req):
        self.requests.append(req)
        headers = Message()
        if self.location is not None:
            headers["Location"] = self.location
        body = json.dumps(self.payload).encode("utf-8")
        response = urllib.response.addinfourl(io.BytesIO(body), headers, req.full_url, self.code)
        response.msg = "OK" if self.code == 200 else "Mock redirect"
        return response


class DiscoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.network_guards = []
        for name in ("socket", "create_connection", "getaddrinfo"):
            self.network_guards.append(self.stack.enter_context(patch.object(
                SUBJECT.socket, name, side_effect=AssertionError("real network forbidden"))))
        self.global_urlopen = self.stack.enter_context(patch.object(
            SUBJECT.urllib.request, "urlopen", side_effect=AssertionError("global urlopen forbidden")))

    def tearDown(self):
        self.global_urlopen.assert_not_called()
        for guard in self.network_guards:
            guard.assert_not_called()

    @contextlib.contextmanager
    def memory_opener(self, handler):
        supplied = []

        def build(*handlers):
            supplied.extend(handlers)
            return REAL_BUILD_OPENER(*handlers, handler)

        with patch.object(SUBJECT.urllib.request, "build_opener", side_effect=build) as factory:
            yield supplied, factory

    def test_supported_origins_are_canonical_and_numeric(self):
        cases = {
            "http://127.0.0.1:9222": "http://127.0.0.1:9222",
            "http://127.0.0.1:9222/": "http://127.0.0.1:9222",
            " HTTP://LOCALHOST:9222/ ": "http://127.0.0.1:9222",
            "http://[::1]:9223/": "http://[::1]:9223",
            "http://localhost": "http://127.0.0.1:80",
            "http://127.0.0.1:1": "http://127.0.0.1:1",
            "http://[::1]:65535": "http://[::1]:65535",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(SUBJECT.local_cdp_endpoint(value), expected)

    def test_invalid_explicit_origin_fails_before_discovery_or_fallback(self):
        cases = (
            "http://example.invalid:9222", "http://127.0.0.2:9222",
            "http://localhost.example.invalid:9222", "http://127.1:9222",
            "http://2130706433:9222", "http://[::ffff:127.0.0.1]:9222",
            "https://127.0.0.1:9222", "ws://127.0.0.1:9222",
            "file:///tmp/mock", "//127.0.0.1:9222",
            "http://user:password@127.0.0.1:9222", "http://@localhost:9222",
            "http://localhost:bad", "http://localhost:65536",
            "http://localhost:0", "http://localhost:-1", "http://localhost:",
            "http://localhost:9222/base", "http://localhost:9222//",
            "http://localhost:9222?token=mock", "http://localhost:9222?",
            "http://localhost:9222#mock", "http://localhost:9222#",
            "http://local\thost:9222", "http://localhost:92\n22",
            "http://localhost:9222/\x7f", "http://[::1", "http://[::1%lo]:9222",
        )
        for value in cases:
            with self.subTest(value=value), patch.dict(os.environ, {
                "CHATGPT_RECOVERY_CDP_ENDPOINT": value,
            }), patch.object(SUBJECT.Path, "glob") as proc_scan, patch.object(
                SUBJECT.urllib.request, "build_opener") as factory, contextlib.redirect_stderr(
                    io.StringIO()) as error:
                with self.assertRaises(SystemExit) as caught:
                    SUBJECT.choose_target()
                self.assertEqual(caught.exception.code, 2)
                self.assertIn("must be a local HTTP origin", error.getvalue())
                self.assertNotIn(value, error.getvalue())
                proc_scan.assert_not_called()
                factory.assert_not_called()

    def test_http_boundary_also_revalidates_before_constructing_request(self):
        with patch.object(SUBJECT.urllib.request, "Request") as request, patch.object(
                SUBJECT.urllib.request, "build_opener") as factory:
            for value in ("http://example.invalid:9222", "http://localhost:9222/\x00"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    SUBJECT.http_json(value)
            request.assert_not_called()
            factory.assert_not_called()

    def test_accepted_explicit_origins_make_only_fixed_local_request(self):
        for value, expected in (
                ("http://127.0.0.1:9222/", "http://127.0.0.1:9222"),
                ("http://localhost:9222", "http://127.0.0.1:9222"),
                ("http://[::1]:9223", "http://[::1]:9223")):
            handler = MemoryHTTPHandler()
            with self.subTest(value=value), self.memory_opener(handler), patch.dict(os.environ, {
                    "CHATGPT_RECOVERY_CDP_ENDPOINT": value,
            }):
                endpoint, target = SUBJECT.choose_target()
            self.assertEqual(endpoint, expected)
            self.assertEqual(target, TARGET)
            self.assertEqual(len(handler.requests), 1)
            request = handler.requests[0]
            self.assertEqual(request.full_url, expected + "/json/list")
            self.assertEqual(request.selector, "/json/list")
            self.assertIsNone(request.data)
            self.assertEqual(dict(request.header_items()), {"User-agent": "chatgpt-export-recovery/1",
                                                          "Host": request.host})
            self.assertNotIn("token", request.full_url)

    def test_all_redirect_codes_and_remote_or_local_locations_are_refused(self):
        for code in (301, 302, 303, 307, 308):
            for location in ("http://example.invalid:9000/remote", "http://127.0.0.1:9223/other",
                             "/another-path", "//example.invalid/remote"):
                handler = MemoryHTTPHandler(code=code, location=location)
                with self.subTest(code=code, location=location), self.memory_opener(handler):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        SUBJECT.http_json("http://127.0.0.1:9222")
                    self.assertEqual(caught.exception.code, code)
                    caught.exception.close()
                self.assertEqual([req.full_url for req in handler.requests],
                                 ["http://127.0.0.1:9222/json/list"])

    def test_environment_proxies_are_disabled_for_discovery(self):
        proxy = "http://proxy.example.invalid:9999"
        env = {name: proxy for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                                       "http_proxy", "https_proxy", "all_proxy")}
        env["NO_PROXY"] = env["no_proxy"] = ""
        handler = MemoryHTTPHandler()
        with patch.dict(os.environ, env, clear=True), self.memory_opener(handler) as (handlers, factory), \
                patch.object(SUBJECT.urllib.request, "getproxies") as system_proxies:
            self.assertEqual(SUBJECT.http_json("http://localhost:9222"), [TARGET])
            system_proxies.assert_not_called()
        factory.assert_called_once()
        proxy_handlers = [h for h in handlers if isinstance(h, urllib.request.ProxyHandler)]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})
        self.assertEqual(len(handler.requests), 1)
        self.assertEqual(handler.requests[0].host, "127.0.0.1:9222")
        self.assertEqual(handler.requests[0].selector, "/json/list")

    def test_existing_remote_websocket_guard_still_precedes_socket(self):
        handler = MemoryHTTPHandler(payload=[dict(TARGET,
            webSocketDebuggerUrl="ws://example.invalid:9222/devtools/page/mock")])
        with self.memory_opener(handler), patch.dict(os.environ, {
                "CHATGPT_RECOVERY_CDP_ENDPOINT": "http://127.0.0.1:9222",
        }), contextlib.redirect_stderr(io.StringIO()) as error:
            _, target = SUBJECT.choose_target()
            with self.assertRaises(SystemExit):
                SUBJECT.WSClient(target["webSocketDebuggerUrl"])
        self.assertIn("CDP WebSocket must be local ws://", error.getvalue())


if __name__ == "__main__":
    unittest.main()
