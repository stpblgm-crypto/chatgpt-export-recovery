"""Exercise local input security with synthetic links and loopback HTTP only."""

import contextlib
from http import HTTPStatus
import io
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

from chatgpt_export_recovery import __main__ as app
from chatgpt_export_recovery.core import RecoveryError, Result
from chatgpt_export_recovery.local_input import _InputServer, read_browser_url


class LocalInputHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = _InputServer()
        self.worker = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.worker.start()
        self.url = self.server.origin + self.server.input_path
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.secret = "https://example.invalid/export?signature=private-test"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)

    def request(self, data=None, headers=None, url=None):
        request = urllib.request.Request(url or self.url, data=data, headers=headers or {})
        try:
            response = self.client.open(request, timeout=2)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read().decode()

    def post(self, value=None, **headers):
        body = urllib.parse.urlencode({"url": value or self.secret}).encode()
        options = {"Origin": self.server.origin,
                   "Content-Type": "application/x-www-form-urlencoded", **headers}
        return self.request(body, options)

    def test_loopback_masked_form_is_not_cached(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, headers, body = self.request()
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIn("type='password'", body)
        self.assertIn(self.server.session_code, body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "same-origin")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertNotIn(self.secret, body)

    def test_accepts_once_without_echoing_link(self):
        status, _, body = self.post()
        self.assertEqual(status, HTTPStatus.OK)
        self.assertNotIn(self.secret, body)
        self.assertEqual(self.server.received.get_nowait(), self.secret)
        self.assertEqual(self.post()[0], HTTPStatus.CONFLICT)
        self.assertTrue(self.server.received.empty())

    def test_rejects_other_origin(self):
        self.assertEqual(self.post(Origin="https://attacker.invalid")[0], HTTPStatus.FORBIDDEN)
        self.assertTrue(self.server.received.empty())

    def test_rejects_missing_origin(self):
        body = urllib.parse.urlencode({"url": self.secret}).encode()
        status, _, _ = self.request(body, {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertTrue(self.server.received.empty())

    def test_rejects_null_origin(self):
        self.assertEqual(self.post(Origin="null")[0], HTTPStatus.FORBIDDEN)
        self.assertTrue(self.server.received.empty())

    def test_rejects_wrong_host_and_nonce(self):
        self.assertEqual(self.request(headers={"Host": "attacker.invalid"})[0], HTTPStatus.NOT_FOUND)
        self.assertEqual(self.request(url=self.server.origin + "/wrong")[0], HTTPStatus.NOT_FOUND)
        self.assertTrue(self.server.received.empty())

    def test_rejects_non_https_input_without_echo(self):
        invalid = "http://example.invalid/private-error-token"
        status, _, body = self.post(invalid)
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertNotIn(invalid, body)
        self.assertTrue(self.server.received.empty())

    def test_rejects_oversized_body_before_read(self):
        headers = {"Origin": self.server.origin, "Content-Type": "application/x-www-form-urlencoded",
                   "Content-Length": "262145"}
        self.assertEqual(self.request(b"", headers)[0], HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        self.assertTrue(self.server.received.empty())

    def test_rejects_unsupported_content_type(self):
        headers = {"Origin": self.server.origin, "Content-Type": "application/json"}
        self.assertEqual(self.request(b"{}", headers)[0], HTTPStatus.UNSUPPORTED_MEDIA_TYPE)


class LocalInputLifecycleTests(unittest.TestCase):
    def test_timeout_is_bounded_without_a_download(self):
        addresses = []
        with self.assertRaisesRegex(RecoveryError, "No download was started"):
            read_browser_url(timeout=0.05, ready=lambda url, code: addresses.append((url, code)))
        self.assertEqual(len(addresses), 1)

    def test_browser_input_cli_does_not_require_a_tty_or_echo_link(self):
        secret = "https://example.invalid/export?signature=private-test"
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin.isatty", return_value=False), \
                patch("chatgpt_export_recovery.local_input.read_browser_url", return_value=secret), \
                patch.object(app.getpass, "getpass") as prompt, \
                patch.object(app, "Downloader") as downloader, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            downloader.return_value.run.return_value = Result(Path("export.zip"), 128, "a" * 64)
            self.assertEqual(app.main(["--cli", "--url-from-browser", "--output", "export.zip"]), 0)
        prompt.assert_not_called()
        self.assertEqual(downloader.call_args.args[0].url, secret)
        self.assertNotIn(secret, output.getvalue() + errors.getvalue())

    def test_input_modes_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                app.main(["--cli", "--output", "export.zip", "--url-from-browser", "--url-from-clipboard"])
        self.assertEqual(caught.exception.code, 2)

    def test_browser_input_flag_requires_cli(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                app.main(["--url-from-browser"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
