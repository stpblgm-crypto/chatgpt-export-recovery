"""Main/stream failure contracts using fake CDP calls and synthetic URL files."""
import base64
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HELPER = Path(__file__).resolve().parents[1] / "src/browsers/chromium_cdp_fetch.py"
SPEC = importlib.util.spec_from_file_location("cdp_main_subject", HELPER)
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)
SYNTHETIC = "SYNTHETIC_ONLY_DO_NOT_LOG"


class MainOfflineTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.secret = self.root / "url.fixture"
        self.secret.write_text("https://chatgpt.com/synthetic?token=" + SYNTHETIC)
        self.secret.chmod(0o600)
        self.output = self.root / "segment"
        self.headers = self.root / "headers"
        self.args = [str(HELPER), "--url-file", str(self.secret), "--start", "4",
                     "--end", "7", "--output", str(self.output), "--headers", str(self.headers)]
        self.resource = {"httpStatusCode": 206, "stream": "fake-stream", "headers": {
            "Content-Range": "bytes 4-7/8", "Content-Length": "4"}}
        self.chunks = [{"data": base64.b64encode(b"\x00\xffAB").decode(),
                        "base64Encoded": True, "eof": True}]
        self.frame = "fake-frame"
        self.read_error = None
        self.calls = []
        self.closed = False
        self.client = Mock()
        self.client.call.side_effect = self.call
        self.client.close.side_effect = lambda: setattr(self, "closed", True)
        self.choose = self.stack.enter_context(patch.object(SUBJECT, "choose_target", return_value=(
            "http://127.0.0.1:1", {"webSocketDebuggerUrl": "ws://127.0.0.1:1/fake"})))
        self.stack.enter_context(patch.object(SUBJECT, "WSClient", return_value=self.client))
        for name in ("socket", "create_connection", "getaddrinfo"):
            self.stack.enter_context(patch.object(SUBJECT.socket, name,
                side_effect=AssertionError("actual socket/DNS forbidden")))
        self.stack.enter_context(patch.object(SUBJECT, "candidate_endpoints",
            side_effect=AssertionError("browser discovery forbidden")))

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": self.frame}}}
        if method == "Network.loadNetworkResource":
            return {"resource": self.resource}
        if method == "IO.read":
            if self.read_error:
                raise self.read_error
            return self.chunks.pop(0)
        return {}

    def invoke(self):
        self.diagnostics = io.StringIO()
        with patch.object(sys, "argv", self.args), contextlib.redirect_stderr(self.diagnostics):
            try:
                return SUBJECT.main()
            finally:
                self.assertNotIn(SYNTHETIC, self.diagnostics.getvalue())

    def assert_cleanup(self, stream=True):
        self.assertTrue(self.closed)
        if stream:
            self.assertIn(("IO.close", {"handle": "fake-stream"}), self.calls)
        self.assertEqual(self.calls[-1], ("Network.setExtraHTTPHeaders", {"headers": {}}))

    def test_exact_binary_segment_and_cleanup(self):
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.output.read_bytes(), b"\x00\xffAB")
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.headers.stat().st_mode & 0o777, 0o600)
        self.assertIn(("Network.loadNetworkResource", {"frameId": "fake-frame",
            "url": self.secret.read_text(), "options": {"disableCache": True,
            "includeCredentials": True}}), self.calls)
        self.assert_cleanup()

    def test_non_206_statuses_never_stream(self):
        for status in (200, 401, 403, 404, 500):
            with self.subTest(status=status):
                self.resource["httpStatusCode"] = status
                self.calls.clear()
                self.assertEqual(self.invoke(), 22 if status in (401, 403) else 24)
                self.assertFalse(self.output.exists())
                self.assertFalse(any(m == "IO.read" for m, _ in self.calls))
                self.assert_cleanup()

    def test_missing_stream_never_creates_output(self):
        self.resource.pop("stream")
        self.assertEqual(self.invoke(), 24)
        self.assertFalse(self.output.exists())
        self.assert_cleanup(stream=False)

    def test_oversized_body_is_rejected_before_write(self):
        self.chunks = [{"data": "ABCDE", "eof": True}]
        self.assertEqual(self.invoke(), 23)
        self.assertEqual(self.output.read_bytes(), b"")
        self.assert_cleanup()

    def test_late_overflow_keeps_only_bounded_uncommitted_segment(self):
        self.chunks = [{"data": "AB", "eof": False}, {"data": "CDE", "eof": True}]
        self.assertEqual(self.invoke(), 23)
        self.assertEqual(self.output.read_bytes(), b"AB")
        self.assert_cleanup()

    def test_invalid_base64_is_rejected_and_closed(self):
        self.chunks = [{"data": "***", "base64Encoded": True, "eof": True}]
        self.assertEqual(self.invoke(), 23)
        self.assertEqual(self.output.read_bytes(), b"")
        self.assert_cleanup()

    def test_unrepresentable_binary_is_rejected_and_closed(self):
        self.chunks = [{"data": "\u2603", "eof": True}]
        with self.assertRaises(SystemExit) as caught:
            self.invoke()
        self.assertEqual(caught.exception.code, 2)
        self.assert_cleanup()

    def test_remote_error_details_are_not_logged(self):
        self.read_error = RuntimeError(SYNTHETIC)
        self.assertEqual(self.invoke(), 23)
        self.assertIn("RuntimeError", self.diagnostics.getvalue())
        self.assert_cleanup()

    def test_interruption_still_releases_stream_and_headers(self):
        self.read_error = KeyboardInterrupt(SYNTHETIC)
        with self.assertRaises(KeyboardInterrupt):
            self.invoke()
        self.assert_cleanup()

    def test_missing_frame_stops_before_resource_fetch(self):
        self.frame = None
        with self.assertRaises(SystemExit) as caught:
            self.invoke()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(any(m == "Network.loadNetworkResource" for m, _ in self.calls))
        self.assert_cleanup(stream=False)

    def test_invalid_range_stops_before_discovery(self):
        self.args[self.args.index("--start") + 1] = "-1"
        with self.assertRaises(SystemExit):
            self.invoke()
        self.choose.assert_not_called()
        self.assertFalse(self.closed)

    def test_permissive_synthetic_url_file_stops_before_discovery(self):
        self.secret.chmod(0o644)
        with self.assertRaises(SystemExit):
            self.invoke()
        self.choose.assert_not_called()
        self.assertFalse(self.closed)

    def test_stream_close_failure_does_not_skip_final_reset(self):
        original = self.call
        def call(method, params=None):
            if method == "IO.close":
                raise RuntimeError(SYNTHETIC)
            return original(method, params)
        self.client.call.side_effect = call
        self.assertEqual(self.invoke(), 0)
        self.assert_cleanup(stream=False)


if __name__ == "__main__":
    unittest.main()
