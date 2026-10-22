"""Synthetic transport / controller contracts, without a display or live services."""

import contextlib
import io
from pathlib import Path
import ssl
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from chatgpt_export_recovery.__main__ import main
from chatgpt_export_recovery.controller import Job
from chatgpt_export_recovery.core import (
    Config, HTTPTransport, Progress, RecoveryError, Result, RetryableError,
    _HTTPSRedirect, validate_url,
)


class URLTests(unittest.TestCase):
    def test_only_safe_https_links(self):
        self.assertEqual(validate_url("https://example.test/archive?token=synthetic"), "https://example.test/archive?token=synthetic")
        for value in ("http://example.test/file", "file:///tmp/file", "https://user:pass@example.test/file",
                      "https://example.test/file#fragment", "https://example.test:444/file", "https:///file",
                      "https://example.test/a\nb", "https://example.test/a b", "https://example.test:bad/file"):
            with self.subTest(value=value), self.assertRaises(RecoveryError) as raised:
                validate_url(value)
            self.assertNotIn(value, str(raised.exception))

    def test_config_repr_hides_url(self):
        self.assertNotIn("SYNTHETIC_SECRET", repr(Config("https://example.test/SYNTHETIC_SECRET", Path("out.zip"))))

    def test_redirect_rejects_downgrade_and_credentials(self):
        handler = _HTTPSRedirect()
        request = urllib.request.Request("https://example.test/file")
        for location in ("http://example.test/file", "file:///etc/passwd", "https://user:pass@example.test/file"):
            with self.subTest(location=location), self.assertRaises(RecoveryError):
                handler.redirect_request(request, None, 302, "Found", {}, location)

    def test_redirect_keeps_range_without_credentials(self):
        request = urllib.request.Request("https://example.test/file", headers={"Range": "bytes=0-99", "If-Range": '"abc"'})
        result = _HTTPSRedirect().redirect_request(request, None, 302, "Found", {}, "https://files.example.test/file")
        self.assertEqual(result.get_header("Range"), "bytes=0-99")
        self.assertEqual(result.get_header("If-range"), '"abc"')
        self.assertIsNone(result.get_header("Cookie"))
        self.assertIsNone(result.get_header("Authorization"))


class TransportTests(unittest.TestCase):
    def test_statuses_sanitized_and_classified(self):
        for code, error_type in ((401, RecoveryError), (403, RecoveryError), (416, RecoveryError),
                                 (404, RecoveryError), (429, RetryableError), (503, RetryableError)):
            transport = HTTPTransport()
            error = urllib.error.HTTPError("https://example.test/SYNTHETIC_SECRET", code,
                                           "SYNTHETIC_SECRET", {}, io.BytesIO(b"private body"))
            with self.subTest(code=code), patch.object(transport.opener, "open", side_effect=error):
                with self.assertRaises(error_type) as raised:
                    transport.open("https://example.test/archive", 0, 0, None, 1)
                self.assertNotIn("SYNTHETIC_SECRET", str(raised.exception))
                self.assertTrue(error.closed)

    def test_certificate_failure_is_not_retryable(self):
        transport = HTTPTransport()
        with patch.object(transport.opener, "open", side_effect=urllib.error.URLError(ssl.SSLError("SYNTHETIC_SECRET"))):
            with self.assertRaises(RecoveryError) as raised:
                transport.open("https://example.test/file", 0, 0, None, 1)
            self.assertNotIsInstance(raised.exception, RetryableError)
            self.assertNotIn("SYNTHETIC_SECRET", str(raised.exception))

    def test_request_contains_only_bounded_headers(self):
        transport = HTTPTransport()
        with patch.object(transport.opener, "open") as opened:
            transport.open("https://example.test/file", 12, 34, '"tag"', 9)
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("Range"), "bytes=12-34")
        self.assertEqual(request.get_header("Accept-encoding"), "identity")
        self.assertIsNone(request.get_header("Cookie"))
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(opened.call_args.kwargs, {"timeout": 9})


class ControllerTests(unittest.TestCase):
    def test_busy_rejects_double_start_cancel_and_restart(self):
        entered = threading.Event()
        release = threading.Event()
        job = Job()

        def run(instance):
            entered.set()
            release.wait(5)
            instance.progress(Progress("downloading", 2, 3, "saved"))
            return Result(Path("out.zip"), 3, "a" * 64)

        with patch("chatgpt_export_recovery.controller.Downloader.run", run):
            self.assertTrue(job.start(Config("https://example.test/file", Path("out.zip"))))
            self.assertTrue(entered.wait(3))
            self.assertFalse(job.start(Config("https://example.test/file", Path("out.zip"))))
            job.pause()
            self.assertTrue(job.cancel_event.is_set())
            release.set()
            job.thread.join(3)
            self.assertFalse(job.busy)
            kinds = []
            while not job.events.empty():
                kinds.append(job.events.get_nowait()[0])
            self.assertEqual(kinds, ["progress", "complete"])
            self.assertTrue(job.start(Config("https://example.test/file", Path("out.zip"))))
            job.thread.join(3)
            self.assertFalse(job.cancel_event.is_set())

    def test_unexpected_exception_never_leaks(self):
        job = Job()
        with patch("chatgpt_export_recovery.controller.Downloader.run", side_effect=RuntimeError("SYNTHETIC_SECRET")):
            job.start(Config("https://example.test/file", Path("out.zip")))
            job.thread.join(3)
        kind, message = job.events.get_nowait()
        self.assertEqual(kind, "error")
        self.assertNotIn("SYNTHETIC_SECRET", message)

    def test_cli_noninteractive_rejects_secret_input(self):
        with patch("sys.stdin.isatty", return_value=False), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["--cli", "--output", "out.zip"]), 2)
        self.assertIn("interactive terminal", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
