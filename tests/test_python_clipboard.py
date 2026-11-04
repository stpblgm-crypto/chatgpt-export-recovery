"""Check explicit clipboard input without a real clipboard or network."""

import contextlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from chatgpt_export_recovery import __main__ as app
from chatgpt_export_recovery.clipboard import read_clipboard_url
from chatgpt_export_recovery.core import RecoveryError, Result


class ClipboardInputTests(unittest.TestCase):
    def reader(self, value):
        root = Mock()
        root.clipboard_get.return_value = value
        module = SimpleNamespace(Tk=Mock(return_value=root))
        return root, module

    def test_reads_once_and_does_not_modify_clipboard(self):
        secret = "https://example.invalid/export?signature=private-test"
        root, module = self.reader(" \n" + secret + "\n")
        with patch.dict(sys.modules, {"tkinter": module}):
            self.assertEqual(read_clipboard_url(), secret)
        root.clipboard_get.assert_called_once_with()
        root.withdraw.assert_called_once_with()
        root.destroy.assert_called_once_with()
        root.clipboard_clear.assert_not_called()
        root.clipboard_append.assert_not_called()

    def test_native_error_never_exposes_clipboard_contents(self):
        root, module = self.reader("")
        root.clipboard_get.side_effect = RuntimeError("private-native-error-token")
        with patch.dict(sys.modules, {"tkinter": module}):
            with self.assertRaises(RecoveryError) as caught:
                read_clipboard_url()
        self.assertNotIn("private-native-error-token", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        root.destroy.assert_called_once_with()

    def test_missing_tk_is_a_safe_input_error(self):
        with patch.dict(sys.modules, {"tkinter": None}):
            with self.assertRaisesRegex(RecoveryError, "clipboard is unavailable"):
                read_clipboard_url()

    def test_non_link_is_rejected_without_echo(self):
        secret = "curl --header Cookie:private-test-token"
        root, module = self.reader(secret)
        with patch.dict(sys.modules, {"tkinter": module}):
            with self.assertRaises(RecoveryError) as caught:
                read_clipboard_url()
        self.assertNotIn(secret, str(caught.exception))
        root.destroy.assert_called_once_with()

    def test_oversized_input_is_rejected(self):
        root, module = self.reader("https://example.invalid/" + "x" * 65536)
        with patch.dict(sys.modules, {"tkinter": module}):
            with self.assertRaisesRegex(RecoveryError, "Copy one complete HTTPS"):
                read_clipboard_url()
        root.destroy.assert_called_once_with()

    def test_explicit_clipboard_mode_works_without_a_tty_and_hides_link(self):
        secret = "https://example.invalid/export?signature=private-test"
        output, errors = io.StringIO(), io.StringIO()
        result = Result(Path("export.zip"), 128, "a" * 64)
        with patch("sys.stdin.isatty", return_value=False), \
                patch("chatgpt_export_recovery.clipboard.read_clipboard_url", return_value=secret) as read, \
                patch.object(app.getpass, "getpass") as prompt, \
                patch.object(app, "Downloader") as downloader, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            downloader.return_value.run.return_value = result
            self.assertEqual(app.main(["--cli", "--url-from-clipboard", "--output", "export.zip"]), 0)
        read.assert_called_once_with()
        prompt.assert_not_called()
        self.assertEqual(downloader.call_args.args[0].url, secret)
        self.assertNotIn(secret, output.getvalue() + errors.getvalue())
        self.assertIn("a" * 64, output.getvalue())

    def test_default_cli_still_requires_hidden_terminal_input(self):
        with patch("sys.stdin.isatty", return_value=False), \
                patch("chatgpt_export_recovery.clipboard.read_clipboard_url") as read, \
                patch.object(app, "Downloader") as downloader, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(["--cli", "--output", "export.zip"]), 2)
        read.assert_not_called()
        downloader.assert_not_called()

    def test_clipboard_flag_requires_cli_mode(self):
        with patch("chatgpt_export_recovery.clipboard.read_clipboard_url") as read, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                app.main(["--url-from-clipboard"])
        self.assertEqual(caught.exception.code, 2)
        read.assert_not_called()

    def test_clipboard_failure_stops_before_any_network_work(self):
        with patch("chatgpt_export_recovery.clipboard.read_clipboard_url",
                   side_effect=RecoveryError("The local desktop clipboard is unavailable.")), \
                patch.object(app, "Downloader") as downloader, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(app.main(["--cli", "--url-from-clipboard", "--output", "export.zip"]), 1)
        downloader.assert_not_called()
        self.assertIn("clipboard is unavailable", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
