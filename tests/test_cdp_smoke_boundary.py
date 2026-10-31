"""Verify smoke confinement/retirement with mocks; creates no socket."""
import contextlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parent / "test_chromium_cdp_transport.py"
SPEC = importlib.util.spec_from_file_location("owned_smoke_subject", PATH)
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)
ENDPOINT = "http://127.0.0.1:45678"


class SmokeBoundaryTests(unittest.TestCase):
    def driver(self, source=None):
        self.audit_hooks = []
        self.original_discovery = Mock(side_effect=AssertionError("unowned discovery"))
        self.subject = SimpleNamespace(local_cdp_endpoint=Mock(return_value=ENDPOINT),
                                       candidate_endpoints=self.original_discovery)
        def main():
            self.assertEqual(list(self.subject.candidate_endpoints()), [ENDPOINT])
            self.assertEqual(sys.argv, [str(SMOKE.HELPER), "--start", "4"])
            return 0
        self.subject.main = main
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
        with patch.object(sys, "argv", ["-c", str(SMOKE.HELPER), "--start", "4"]), \
                patch.dict(os.environ, {"CHATGPT_RECOVERY_CDP_ENDPOINT": ENDPOINT}), \
                patch.object(importlib.util, "spec_from_file_location", return_value=spec), \
                patch.object(importlib.util, "module_from_spec", return_value=self.subject), \
                patch.object(sys, "addaudithook", side_effect=self.audit_hooks.append):
            with self.assertRaises(SystemExit) as caught:
                exec(compile(SMOKE.HELPER_DRIVER if source is None else source,
                             "owned-smoke-driver", "exec"), {})
            self.assertEqual(caught.exception.code, 0)

    def test_driver_restricts_candidates_and_forwards_arguments(self):
        self.driver()
        self.original_discovery.assert_not_called()
        self.subject.local_cdp_endpoint.assert_called_once_with(ENDPOINT)

    def test_removing_candidate_restriction_triggers_negative_control(self):
        modified = SMOKE.HELPER_DRIVER.replace(
            "subject.candidate_endpoints = lambda: iter((endpoint,))", "")
        self.assertNotEqual(modified, SMOKE.HELPER_DRIVER)
        with self.assertRaisesRegex(AssertionError, "unowned discovery"):
            self.driver(modified)

    def test_driver_audit_refuses_other_endpoints_and_process_reads(self):
        self.driver()
        self.assertEqual(len(self.audit_hooks), 1)
        audit = self.audit_hooks[0]
        audit("socket.connect", (None, ("127.0.0.1", 45678)))
        audit("socket.getaddrinfo", ("127.0.0.1", 45678, 0, 0, 0))
        for event, args in (
                ("socket.connect", (None, ("127.0.0.1", 9222))),
                ("socket.connect", (None, ("192.0.2.1", 45678))),
                ("socket.bind", (None, ("127.0.0.1", 45678))),
                ("socket.getaddrinfo", ("example.invalid", 45678, 0, 0, 0)),
                ("open", ("/proc/1/cmdline", "r", 0))):
            with self.subTest(event=event, args=args), self.assertRaises(RuntimeError):
                audit(event, args)

    def test_fake_listener_close_joins_all_owned_threads(self):
        sock = Mock()
        sock.getsockname.return_value = ("127.0.0.1", 45678)
        thread = Mock()
        thread.is_alive.return_value = False
        with patch.object(SMOKE.socket, "socket", return_value=sock), \
                patch.object(SMOKE.threading, "Thread", return_value=thread):
            fake = SMOKE.FakeCDP()
            handler = Mock()
            handler.is_alive.return_value = False
            fake.handlers.append(handler)
            fake.close()
        sock.bind.assert_called_once_with(("127.0.0.1", 0))
        sock.close.assert_called_once_with()
        thread.join.assert_called_once_with(3)
        handler.join.assert_called_once_with(3)
        self.assertTrue(fake.stopped.is_set())

    def test_fake_listener_retirement_failure_is_not_green(self):
        sock = Mock()
        sock.getsockname.return_value = ("127.0.0.1", 45678)
        thread = Mock()
        thread.is_alive.return_value = True
        with patch.object(SMOKE.socket, "socket", return_value=sock), \
                patch.object(SMOKE.threading, "Thread", return_value=thread):
            fake = SMOKE.FakeCDP()
            with self.assertRaisesRegex(AssertionError, "did not retire"):
                fake.close()


if __name__ == "__main__":
    unittest.main()
