"""Portable, offline adversarial tests for the Python recovery engine.

All payloads, links, ETags, headers, and errors in this file are synthetic.
No real HTTP request, browser access, credentials, or exported data is used.

Run from the repository root:
    python -m unittest discover -s tests -p 'test_python_core.py' -v
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from email.message import Message
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import ssl
import struct
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.error
import urllib.request
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chatgpt_export_recovery import core  # noqa: E402


SYNTHETIC_URL = "https://download.invalid/export.zip?token=synthetic-secret-value"
SYNTHETIC_TAG = '"synthetic-object-v1"'


def archive_bytes(*, compressed=False, empty=False):
    """A deterministic archive large enough to need several 1 KiB ranges."""
    result = io.BytesIO()
    compression = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
    with zipfile.ZipFile(result, "w", compression=compression) as archive:
        if not empty:
            item = zipfile.ZipInfo("conversations.json", (2020, 1, 1, 0, 0, 0))
            item.compress_type = compression
            archive.writestr(item, bytes(range(256)) * 25)
            item = zipfile.ZipInfo("notes.txt", (2020, 1, 1, 0, 0, 0))
            item.compress_type = compression
            archive.writestr(item, b"Synthetic test payload only.\n")
    return result.getvalue()


class FakeResponse:
    def __init__(self, body=b"", *, status=206, headers=None, reads=None,
                 on_read=None):
        self.status = status
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value
        self.body = io.BytesIO(body)
        self.reads = deque(reads) if reads is not None else None
        self.on_read = on_read
        self.closed = False
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.closed = True

    def read(self, size=-1):
        self.read_sizes.append(size)
        if self.on_read:
            self.on_read()
        if self.reads is not None:
            value = self.reads.popleft() if self.reads else b""
            if isinstance(value, BaseException):
                raise value
            # Scripted reads intentionally may break the declared body length.
            return value
        return self.body.read(size)


class FakeTransport:
    """A Range server with optional per-call faults, wholly in memory."""
    def __init__(self, payload, *, etag=SYNTHETIC_TAG, actions=None):
        self.payload = payload
        self.etag = etag
        self.actions = dict(actions or {})
        self.calls = []
        self.responses = []

    def response(self, start, end, **changes):
        last = min(end, len(self.payload) - 1)
        body = self.payload[start:last + 1]
        headers = {
            "Content-Range": f"bytes {start}-{last}/{len(self.payload)}",
            "Content-Length": str(len(body)),
            "ETag": self.etag,
            "Content-Encoding": "identity",
        }
        headers.update(changes.pop("headers", {}))
        for name in changes.pop("missing_headers", ()):
            headers.pop(name, None)
        return FakeResponse(changes.pop("body", body), headers=headers, **changes)

    def open(self, url, start, end, etag, timeout):
        call = SimpleNamespace(url=url, start=start, end=end, etag=etag,
                               timeout=timeout)
        self.calls.append(call)
        action = self.actions.get(len(self.calls))
        if isinstance(action, BaseException):
            raise action
        response = (action(self, call) if callable(action) else action)
        if response is None:
            response = self.response(start, end)
        self.responses.append(response)
        return response


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="export-recovery-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.output = self.folder / "recovered.zip"
        self.payload = archive_bytes()
        self.events = []
        self.cancel = threading.Event()
        self.transport = FakeTransport(self.payload)
        self.config = core.Config(SYNTHETIC_URL, self.output, chunk_size=1024,
                                  attempts=3, retry_delay=0)
        # A regression must not accidentally use the real production transport.
        patcher = mock.patch.object(core.HTTPTransport, "open",
                                    side_effect=AssertionError("Real HTTP is forbidden in these tests"))
        self.network_guard = patcher.start()
        self.addCleanup(patcher.stop)

    @property
    def workspace(self):
        return self.output.with_name(self.output.name + ".recovery")

    @property
    def part(self):
        return self.workspace / "checkpoint.part"

    @property
    def metadata(self):
        return self.workspace / "state.json"

    def downloader(self, **changes):
        return core.Downloader(replace(self.config, **changes),
                               transport=self.transport, cancel=self.cancel,
                               progress=self.events.append)

    def run_download(self, **changes):
        return self.downloader(**changes).run()

    def state(self):
        return json.loads(self.metadata.read_text(encoding="utf-8"))

    def checkpoint(self, committed=1024, *, suffix=b"", **changes):
        self.workspace.mkdir(exist_ok=True)
        self.part.write_bytes(self.payload[:committed] + suffix)
        state = {
            "version": 1,
            "committed": committed,
            "total": len(self.payload),
            "etag_sha256": hashlib.sha256(self.transport.etag.encode("ascii")).hexdigest(),
            "resource_sha256": hashlib.sha256(b"download.invalid/export.zip").hexdigest(),
            "sha256": hashlib.sha256(self.payload[:committed]).hexdigest(),
        }
        state.update(changes)
        self.metadata.write_text(json.dumps(state), encoding="utf-8")

    def assert_preserved(self, part, metadata):
        self.assertEqual(self.part.read_bytes(), part)
        self.assertEqual(self.metadata.read_bytes(), metadata)
        self.assertFalse(self.output.exists())

    def assert_no_sensitive_output(self, text):
        self.assertNotIn(SYNTHETIC_URL, text)
        self.assertNotIn("synthetic-secret-value", text)
        self.assertNotIn(SYNTHETIC_TAG, text)


class DownloadSuccessTests(BaseTest):
    def test_full_archive_has_exact_bytes_digest_and_bounded_ranges(self):
        result = self.run_download()
        self.assertEqual(result.output, self.output)
        self.assertEqual(result.size, len(self.payload))
        self.assertEqual(result.sha256, hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(self.output.read_bytes(), self.payload)
        self.assertEqual(self.part.read_bytes(), self.payload)
        self.assertEqual(self.state()["committed"], len(self.payload))
        self.assertEqual((self.transport.calls[0].start, self.transport.calls[0].end), (0, 0))
        self.assertIsNone(self.transport.calls[0].etag)
        for call in self.transport.calls[1:]:
            self.assertLessEqual(call.end - call.start + 1, 1024)
            self.assertEqual(call.etag, SYNTHETIC_TAG)
        self.assertTrue(all(r.closed for r in self.transport.responses))
        self.assertEqual(self.events[-1].phase, "complete")

    def test_expected_digest_accepts_surrounding_space_and_uppercase(self):
        expected = hashlib.sha256(self.payload).hexdigest()
        self.assertEqual(self.run_download(expected_sha256=f" {expected.upper()} ").sha256,
                         expected)

    def test_metadata_contains_only_nonsecret_checkpoint_fields(self):
        self.run_download()
        self.assertEqual(set(self.state()),
                         {"version", "committed", "total", "etag_sha256", "resource_sha256", "sha256"})
        self.assert_no_sensitive_output(self.metadata.read_text(encoding="utf-8"))
        self.assert_no_sensitive_output(repr(self.config))
        self.assert_no_sensitive_output(repr(self.events))
        self.assertFalse(list(self.workspace.glob("state-*.tmp")))

    def test_missing_content_length_is_valid_when_body_matches_range(self):
        self.transport.actions[1] = lambda t, c: t.response(c.start, c.end,
                                                           missing_headers=("Content-Length",))
        self.run_download()
        self.assertEqual(self.output.read_bytes(), self.payload)

    def test_uncompressed_encoding_is_case_insensitive(self):
        self.transport.actions[1] = lambda t, c: t.response(c.start, c.end,
                                                           headers={"Content-Encoding": "IDENTITY"})
        self.run_download()

    def test_empty_strong_etag_is_valid(self):
        self.transport.etag = '""'
        self.run_download()


class HeaderValidationTests(BaseTest):
    def reject_probe(self, *, headers=None, status=206, body=None, missing=()):
        transport = FakeTransport(self.payload)
        response = transport.response(0, 0, headers=headers or {}, status=status,
                                      missing_headers=missing,
                                      **({"body": body} if body is not None else {}))
        transport.actions[1] = response
        downloader = core.Downloader(self.config, transport=transport)
        with self.assertRaises(core.RecoveryError):
            downloader._segment(0, 0)
        self.assertEqual(len(transport.calls), 1, "Protocol violations must not be retried")
        self.assertTrue(response.closed)

    def test_non_206_statuses_are_terminal(self):
        for status in (200, 204, 301, 302, 304, 400, 401, 403, 404, 416, 500):
            with self.subTest(status=status):
                self.reject_probe(status=status)

    def test_malformed_content_ranges_are_rejected(self):
        size = len(self.payload)
        ranges = ("", f"bytes 0-0/*", "bytes */10", "bytes -1-0/10",
                  "bytes 1-0/10", "bytes 0-0/0", "bytes 0-0/-1",
                  f"bytes 1-1/{size}", f"bytes 0-1/{size}",
                  f"Bytes 0-0/{size}", f"bytes 0-0/{size}\r\n",
                  f"bytes 0-0/{size}, bytes 1-1/{size}",
                  f"bytes 0 - 0 / {size}", "bytes 0-0/1.5")
        for value in ranges:
            with self.subTest(content_range=value):
                self.reject_probe(headers={"Content-Range": value})

    def test_non_ascii_range_digits_are_rejected(self):
        self.reject_probe(headers={"Content-Range": "bytes ٠-٠/١٠"})

    def test_missing_content_range_is_rejected(self):
        self.reject_probe(missing=("Content-Range",))

    def test_weak_or_malformed_etags_are_rejected(self):
        for tag in ("", 'W/"version"', 'w/"version"', "version", '"a b"',
                    '"a\tb"', '"a\nb"', '"a"b"', '"é"', '"x", "y"'):
            with self.subTest(etag=tag):
                self.reject_probe(headers={"ETag": tag})

    def test_missing_etag_is_rejected(self):
        self.reject_probe(missing=("ETag",))

    def test_compressed_or_multiple_encodings_are_rejected(self):
        for value in ("gzip", "br", "deflate", "identity,gzip", "", " identity "):
            with self.subTest(encoding=value):
                self.reject_probe(headers={"Content-Encoding": value})

    def test_malformed_or_mismatched_lengths_are_rejected(self):
        for value in ("", "0", "2", "-1", "+1", "1.0", "1,1", " 1", "1 ", "1\r\n"):
            with self.subTest(content_length=value):
                self.reject_probe(headers={"Content-Length": value})

    def test_non_ascii_content_length_digits_are_rejected(self):
        self.reject_probe(headers={"Content-Length": "١"})

    def test_oversized_body_is_terminal(self):
        self.reject_probe(body=b"AB")

    def test_changed_etag_preserves_checkpoint(self):
        self.checkpoint()
        before = self.part.read_bytes(), self.metadata.read_bytes()
        self.transport.actions[2] = lambda t, c: t.response(c.start, c.end,
                                                           headers={"ETag": '"changed"'})
        with self.assertRaisesRegex(core.RecoveryError, "archive changed"):
            self.run_download()
        self.assert_preserved(*before)
        self.assertEqual(len(self.transport.calls), 2)

    def test_changed_remote_total_preserves_checkpoint(self):
        self.checkpoint()
        before = self.part.read_bytes(), self.metadata.read_bytes()
        self.transport.actions[2] = lambda t, c: t.response(c.start, c.end,
                        headers={"Content-Range": f"bytes {c.start}-{c.end}/{len(self.payload) + 1}"})
        with self.assertRaisesRegex(core.RecoveryError, "size changed"):
            self.run_download()
        self.assert_preserved(*before)

    def test_invalid_segment_status_does_not_append(self):
        self.transport.actions[2] = lambda t, c: t.response(c.start, c.end, status=200)
        with self.assertRaises(core.RecoveryError):
            self.run_download()
        self.assertEqual(self.part.read_bytes(), b"")
        self.assertEqual(self.state()["committed"], 0)


class RetryAndCancellationTests(BaseTest):
    def test_short_response_retries_exact_range_without_duplicate_append(self):
        self.transport.actions[2] = lambda t, c: t.response(c.start, c.end, body=b"short")
        self.run_download()
        calls = self.transport.calls[1:3]
        self.assertEqual((calls[0].start, calls[0].end), (calls[1].start, calls[1].end))
        self.assertEqual(self.output.read_bytes(), self.payload)
        self.assertEqual(sum(e.phase == "retry" for e in self.events), 1)

    def test_mid_body_read_exception_retries_without_duplicate_append(self):
        for error in (OSError(SYNTHETIC_URL), http.client.IncompleteRead(b"partial"),
                      http.client.RemoteDisconnected(SYNTHETIC_URL)):
            with self.subTest(error=type(error).__name__):
                transport = FakeTransport(self.payload)
                transport.actions[1] = lambda t, c, error=error: t.response(
                    0, 1023, reads=[self.payload[:100], error])
                events = []
                downloader = core.Downloader(self.config, transport=transport, progress=events.append)
                data, size, _ = downloader._segment(0, 1023)
                self.assertEqual(data, self.payload[:1024])
                self.assertEqual(size, len(self.payload))
                self.assertEqual(len(transport.calls), 2)
                self.assert_no_sensitive_output(repr(events))
                self.assertTrue(all(r.closed for r in transport.responses))

    def test_retry_exhaustion_is_bounded_and_retains_committed_prefix(self):
        self.checkpoint()
        before = self.part.read_bytes(), self.metadata.read_bytes()
        self.transport.actions.update({n: OSError(SYNTHETIC_URL) for n in (2, 3, 4)})
        with self.assertRaisesRegex(core.RecoveryError, "bounded retries") as error:
            self.run_download()
        self.assert_preserved(*before)
        self.assertEqual(len(self.transport.calls), 4)
        self.assertEqual({(c.start, c.end) for c in self.transport.calls[1:]}, {(1024, 2047)})
        self.assert_no_sensitive_output(str(error.exception) + repr(self.events))

    def test_retryable_open_error_can_recover(self):
        self.transport.actions[1] = core.RetryableError("Synthetic temporary failure")
        self.run_download()
        self.assertEqual(self.output.read_bytes(), self.payload)

    def test_terminal_open_error_is_not_retried(self):
        self.transport.actions[1] = core.RecoveryError("Synthetic permanent failure")
        with self.assertRaises(core.RecoveryError):
            self.run_download()
        self.assertEqual(len(self.transport.calls), 1)

    def test_precancelled_run_makes_no_network_or_workspace(self):
        self.cancel.set()
        with self.assertRaises(core.Cancelled):
            self.run_download()
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.transport.calls, [])

    def test_cancel_mid_response_does_not_commit_partial_data(self):
        self.transport.actions[2] = lambda t, c: t.response(c.start, c.end,
                                                           on_read=self.cancel.set)
        with self.assertRaises(core.Cancelled):
            self.run_download()
        self.assertEqual(self.part.read_bytes(), b"")
        self.assertEqual(self.state()["committed"], 0)
        self.assertFalse(self.output.exists())

    def test_cancel_after_commit_then_resume_starts_at_exact_offset(self):
        def progress(event):
            if event.phase == "downloading":
                self.cancel.set()
        downloader = core.Downloader(self.config, transport=self.transport,
                                     cancel=self.cancel, progress=progress)
        with self.assertRaises(core.Cancelled):
            downloader.run()
        self.assertEqual(self.part.read_bytes(), self.payload[:1024])
        self.assertEqual(self.state()["committed"], 1024)
        self.cancel.clear()
        self.transport = FakeTransport(self.payload)
        self.run_download()
        self.assertEqual(self.transport.calls[1].start, 1024)
        self.assertEqual(self.output.read_bytes(), self.payload)

    def test_cancel_during_backoff_stops_before_retry(self):
        self.transport.actions[1] = core.RetryableError("Synthetic temporary failure")
        def progress(event):
            if event.phase == "retry":
                self.cancel.set()
        downloader = core.Downloader(self.config, transport=self.transport,
                                     cancel=self.cancel, progress=progress)
        with self.assertRaises(core.Cancelled):
            downloader.run()
        self.assertEqual(len(self.transport.calls), 1)

    def test_cancel_before_verification_keeps_full_checkpoint(self):
        def progress(event):
            if event.phase == "verifying":
                self.cancel.set()
        downloader = core.Downloader(self.config, transport=self.transport,
                                     cancel=self.cancel, progress=progress)
        with self.assertRaises(core.Cancelled):
            downloader.run()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.part.read_bytes(), self.payload)
        self.cancel.clear()
        self.transport = FakeTransport(self.payload)
        self.run_download()
        self.assertEqual(len(self.transport.calls), 1, "A complete checkpoint needs only a probe")


class CheckpointTests(BaseTest):
    def test_checkpoint_prefix_tamper_is_detected_before_append(self):
        self.checkpoint(suffix=b"uncommitted crash suffix")
        data = bytearray(self.part.read_bytes())
        data[12] ^= 0x55
        self.part.write_bytes(data)
        before = self.part.read_bytes(), self.metadata.read_bytes()
        with self.assertRaisesRegex(core.RecoveryError, "integrity failed"):
            self.run_download()
        self.assert_preserved(*before)
        self.assertEqual(len(self.transport.calls), 1)

    def test_crash_suffix_is_discarded_only_after_prefix_verification(self):
        self.checkpoint(suffix=b"not committed")
        self.run_download()
        self.assertEqual(self.transport.calls[1].start, 1024)
        self.assertEqual(self.output.read_bytes(), self.payload)

    def test_zero_commit_with_crash_suffix_recovers(self):
        self.checkpoint(committed=0, suffix=b"written before crash")
        self.run_download()
        self.assertEqual(self.output.read_bytes(), self.payload)

    def test_incomplete_checkpoint_file_is_rejected_unchanged(self):
        self.checkpoint()
        self.part.write_bytes(self.payload[:100])
        before = self.part.read_bytes(), self.metadata.read_bytes()
        with self.assertRaises(core.RecoveryError):
            self.run_download()
        self.assert_preserved(*before)

    def test_invalid_committed_types_and_bounds_are_rejected(self):
        for offset in (-1, len(self.payload) + 1, True, False, 1.0, "1024", None):
            with self.subTest(offset=offset):
                self.checkpoint()
                state = self.state()
                state["committed"] = offset
                self.metadata.write_text(json.dumps(state), encoding="utf-8")
                before = self.part.read_bytes(), self.metadata.read_bytes()
                with self.assertRaises(core.RecoveryError):
                    self.run_download()
                self.assert_preserved(*before)

    def test_missing_required_state_fields_are_rejected(self):
        for name in ("version", "committed", "total", "etag_sha256", "resource_sha256", "sha256"):
            with self.subTest(field=name):
                self.checkpoint()
                state = self.state()
                del state[name]
                self.metadata.write_text(json.dumps(state), encoding="utf-8")
                before = self.part.read_bytes(), self.metadata.read_bytes()
                with self.assertRaises(core.RecoveryError):
                    self.run_download()
                self.assert_preserved(*before)

    def test_changed_probe_identity_rejects_saved_checkpoint(self):
        self.checkpoint()
        before = self.part.read_bytes(), self.metadata.read_bytes()
        self.transport.etag = '"different-object"'
        with self.assertRaisesRegex(core.RecoveryError, "saved export"):
            self.run_download()
        self.assert_preserved(*before)

    def test_nonobject_json_metadata_raises_safe_recovery_error(self):
        for state in ([], None, "synthetic metadata", 12, True):
            with self.subTest(state=state):
                self.checkpoint()
                self.metadata.write_text(json.dumps(state), encoding="utf-8")
                before = self.part.read_bytes(), self.metadata.read_bytes()
                with self.assertRaises(core.RecoveryError):
                    self.run_download()
                self.assert_preserved(*before)

    def test_malformed_and_oversized_metadata_are_preserved(self):
        for content in (b"{not-json", b"\xff\xfe", b" " * 4097):
            with self.subTest(content=content[:20]):
                self.checkpoint()
                self.metadata.write_bytes(content)
                before = self.part.read_bytes(), self.metadata.read_bytes()
                with self.assertRaises(core.RecoveryError):
                    self.run_download()
                self.assert_preserved(*before)

    def test_orphan_checkpoint_is_never_overwritten(self):
        self.workspace.mkdir()
        self.part.write_bytes(b"orphan synthetic checkpoint")
        with self.assertRaisesRegex(core.RecoveryError, "without metadata"):
            self.run_download()
        self.assertEqual(self.part.read_bytes(), b"orphan synthetic checkpoint")
        self.assertFalse(self.metadata.exists())

    def test_metadata_without_checkpoint_is_preserved(self):
        self.checkpoint()
        self.part.unlink()
        before = self.metadata.read_bytes()
        with self.assertRaises(core.RecoveryError):
            self.run_download()
        self.assertEqual(self.metadata.read_bytes(), before)
        self.assertFalse(self.part.exists())

    def test_concurrent_checkpoint_append_is_detected_before_write(self):
        def append_unexpected(t, call):
            with self.part.open("ab") as file:
                file.write(b"external write")
            return t.response(call.start, call.end)
        self.transport.actions[2] = append_unexpected
        with self.assertRaisesRegex(core.RecoveryError, "changed while downloading"):
            self.run_download()
        self.assertEqual(self.part.read_bytes(), b"external write")
        self.assertEqual(self.state()["committed"], 0)
        self.assertFalse(self.output.exists())

    def test_failed_metadata_commit_leaves_recoverable_suffix(self):
        original = core._save_state
        def fail_second_commit(path, state):
            if state["committed"]:
                raise OSError("Synthetic disk failure " + SYNTHETIC_URL)
            original(path, state)
        with mock.patch.object(core, "_save_state", side_effect=fail_second_commit):
            with self.assertRaises(core.RecoveryError) as error:
                self.run_download()
        self.assert_no_sensitive_output(str(error.exception))
        self.assertEqual(self.state()["committed"], 0)
        self.assertEqual(self.part.read_bytes(), self.payload[:1024])
        self.transport = FakeTransport(self.payload)
        self.run_download()
        self.assertEqual(self.output.read_bytes(), self.payload)


class ImportTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.source = self.folder / "browser-download.crdownload"

    def test_imported_prefix_is_verified_and_original_preserved(self):
        original = self.payload[:1700]
        self.source.write_bytes(original)
        self.run_download(import_partial=self.source)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.output.read_bytes(), self.payload)
        importing = [e.completed for e in self.events if e.phase == "importing"]
        self.assertEqual(importing, [1024, 1700])
        self.assertEqual(self.transport.calls[2].end, 1699)
        self.assertEqual(self.transport.calls[3].start, 1700)

    def test_first_prefix_mismatch_preserves_original_and_empty_checkpoint(self):
        original = b"wrong synthetic partial" * 60
        self.source.write_bytes(original)
        with self.assertRaisesRegex(core.RecoveryError, "does not match"):
            self.run_download(import_partial=self.source)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.part.read_bytes(), b"")
        self.assertEqual(self.state()["committed"], 0)
        self.assertFalse(self.output.exists())

    def test_later_prefix_mismatch_preserves_only_verified_checkpoint(self):
        original = self.payload[:1024] + b"wrong second segment" * 30
        self.source.write_bytes(original)
        with self.assertRaisesRegex(core.RecoveryError, "does not match"):
            self.run_download(import_partial=self.source)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.part.read_bytes(), self.payload[:1024])
        self.assertEqual(self.state()["committed"], 1024)

    def test_too_large_partial_is_rejected_without_modification(self):
        original = self.payload + b"too large"
        self.source.write_bytes(original)
        with self.assertRaisesRegex(core.RecoveryError, "larger"):
            self.run_download(import_partial=self.source)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.part.read_bytes(), b"")
        self.assertEqual(len(self.transport.calls), 1)

    def test_full_existing_archive_imports_without_modifying_source(self):
        self.source.write_bytes(self.payload)
        self.run_download(import_partial=self.source)
        self.assertEqual(self.source.read_bytes(), self.payload)
        self.assertEqual(self.output.read_bytes(), self.payload)
        self.assertFalse(any(e.phase == "downloading" for e in self.events))

    def test_missing_import_source_does_not_publish(self):
        with self.assertRaisesRegex(core.RecoveryError, "existing browser partial"):
            self.run_download(import_partial=self.source)
        self.assertFalse(self.source.exists())
        self.assertFalse(self.output.exists())

    def test_import_cannot_be_own_checkpoint(self):
        with self.assertRaisesRegex(core.RecoveryError, "separate from the output"):
            self.run_download(import_partial=self.part)
        self.assertEqual(self.part.read_bytes(), b"")


class StorageAndLockTests(BaseTest):
    def test_existing_output_is_never_overwritten_or_downloaded(self):
        self.output.write_bytes(b"existing synthetic file")
        with self.assertRaisesRegex(core.RecoveryError, "already exists"):
            self.run_download()
        self.assertEqual(self.output.read_bytes(), b"existing synthetic file")
        self.assertEqual(self.transport.calls, [])

    def test_output_appearing_at_publication_is_not_overwritten(self):
        real_link = os.link
        def race_link(source, destination):
            Path(destination).write_bytes(b"concurrent synthetic output")
            real_link(source, destination)
        with mock.patch.object(core.os, "link", side_effect=race_link):
            with self.assertRaisesRegex(core.RecoveryError, "appeared during download"):
                self.run_download()
        self.assertEqual(self.output.read_bytes(), b"concurrent synthetic output")
        self.assertEqual(self.part.read_bytes(), self.payload)

    def test_unsupported_hard_link_retains_verified_checkpoint(self):
        with mock.patch.object(core.os, "link", side_effect=OSError("Synthetic unsupported filesystem")):
            with self.assertRaisesRegex(core.RecoveryError, "atomically publish"):
                self.run_download()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.part.read_bytes(), self.payload)
        self.assertEqual(self.state()["committed"], len(self.payload))

    def test_lock_prevents_second_writer_and_is_reusable_after_close(self):
        self.workspace.mkdir()
        first = core._OutputLock(self.workspace / "lock")
        try:
            with self.assertRaisesRegex(core.RecoveryError, "Another recovery"):
                self.run_download()
            self.assertEqual(self.transport.calls, [])
            self.assertFalse(self.part.exists())
        finally:
            first.close()
        self.run_download()
        self.assertTrue((self.workspace / "lock").exists())

    def test_lock_is_released_after_error(self):
        self.transport.actions[1] = core.RecoveryError("Synthetic probe failure")
        with self.assertRaises(core.RecoveryError):
            self.run_download()
        self.transport.actions.clear()
        self.run_download()

    def test_low_disk_space_keeps_empty_checkpoint(self):
        with mock.patch.object(core.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(core.RecoveryError, "free disk space"):
                self.run_download()
        self.assertEqual(self.part.read_bytes(), b"")
        self.assertEqual(self.state()["committed"], 0)
        self.assertEqual(len(self.transport.calls), 1)

    def test_recovery_folder_symlink_is_rejected(self):
        target = self.folder / "private-target"
        target.mkdir()
        try:
            self.workspace.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symbolic links is not permitted on this platform")
        with self.assertRaisesRegex(core.RecoveryError, "symbolic link"):
            self.run_download()
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(self.transport.calls, [])

    def test_checkpoint_symlink_is_rejected_without_touching_target(self):
        self.workspace.mkdir()
        target = self.folder / "untouched.bin"
        target.write_bytes(b"preserve me")
        try:
            self.part.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symbolic links is not permitted on this platform")
        with self.assertRaisesRegex(core.RecoveryError, "regular file"):
            self.run_download()
        self.assertEqual(target.read_bytes(), b"preserve me")
        self.assertEqual(self.transport.calls, [])

    def test_directory_as_checkpoint_is_rejected(self):
        self.workspace.mkdir()
        self.part.mkdir()
        with self.assertRaisesRegex(core.RecoveryError, "regular file"):
            self.run_download()
        self.assertEqual(self.transport.calls, [])


class IntegrityTests(BaseTest):
    def test_sha256_mismatch_keeps_full_checkpoint_unpublished(self):
        with self.assertRaisesRegex(core.RecoveryError, "SHA256 verification failed"):
            self.run_download(expected_sha256="0" * 64)
        self.assertEqual(self.part.read_bytes(), self.payload)
        self.assertEqual(self.state()["committed"], len(self.payload))
        self.assertFalse(self.output.exists())

    def test_same_size_tamper_before_final_hash_is_detected(self):
        def progress(event):
            if event.phase == "verifying":
                data = bytearray(self.part.read_bytes())
                data[80] ^= 1
                self.part.write_bytes(data)
        downloader = core.Downloader(self.config, transport=self.transport, progress=progress)
        with self.assertRaisesRegex(core.RecoveryError, "SHA256 verification failed"):
            downloader.run()
        self.assertFalse(self.output.exists())

    def test_non_zip_response_is_never_published(self):
        self.payload = b"Synthetic HTML login page, not a ZIP" * 100
        self.transport = FakeTransport(self.payload)
        with self.assertRaisesRegex(core.RecoveryError, "ZIP integrity"):
            self.run_download()
        self.assertEqual(self.part.read_bytes(), self.payload)
        self.assertFalse(self.output.exists())

    def test_empty_zip_is_rejected(self):
        self.payload = archive_bytes(empty=True)
        self.transport = FakeTransport(self.payload)
        with self.assertRaisesRegex(core.RecoveryError, "empty ZIP"):
            self.run_download()
        self.assertFalse(self.output.exists())

    def test_corrupt_entry_crc_is_rejected(self):
        data = bytearray(self.payload)
        name_length, extra_length = struct.unpack_from("<HH", data, 26)
        data[30 + name_length + extra_length + 40] ^= 1
        self.payload = bytes(data)
        self.transport = FakeTransport(self.payload)
        with self.assertRaisesRegex(core.RecoveryError, "ZIP integrity"):
            self.run_download()
        self.assertEqual(self.part.read_bytes(), self.payload)
        self.assertFalse(self.output.exists())

    def test_corrupt_deflate_stream_raises_safe_recovery_error(self):
        data = bytearray(archive_bytes(compressed=True))
        name_length, extra_length = struct.unpack_from("<HH", data, 26)
        # BTYPE=3 is explicitly invalid in the DEFLATE stream format.
        data[30 + name_length + extra_length] = 0x07
        self.payload = bytes(data)
        self.transport = FakeTransport(self.payload)
        with self.assertRaisesRegex(core.RecoveryError, "ZIP integrity"):
            self.run_download()
        self.assertEqual(self.part.read_bytes(), self.payload)
        self.assertFalse(self.output.exists())

    def test_cancel_during_zip_verification_does_not_publish(self):
        downloader = self.downloader()
        original = downloader._verify_zip
        def cancel_then_verify(part):
            self.cancel.set()
            original(part)
        with mock.patch.object(downloader, "_verify_zip", side_effect=cancel_then_verify):
            with self.assertRaises(core.Cancelled):
                downloader.run()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.part.read_bytes(), self.payload)


class InputValidationTests(BaseTest):
    def test_unsafe_urls_are_rejected_before_transport(self):
        values = ("http://download.invalid/a", "ftp://download.invalid/a", "file:///tmp/a",
                  "https://", "https:///missing-host", "https://user:pass@download.invalid/a",
                  "https://user@download.invalid/a", "https://download.invalid:444/a",
                  "https://download.invalid/a#fragment", "https://download.invalid/a b",
                  "https://download.invalid/a\n", "https://döwnload.invalid/a", "", None)
        for url in values:
            with self.subTest(url=url):
                with self.assertRaises(core.RecoveryError):
                    self.run_download(url=url)
                self.assertEqual(self.transport.calls, [])

    def test_https_explicit_standard_port_is_valid(self):
        self.assertEqual(core.validate_url("https://download.invalid:443/a?b=c"),
                         "https://download.invalid:443/a?b=c")

    def test_invalid_download_settings_reject_before_network(self):
        for change in ({"chunk_size": 0}, {"chunk_size": 1023},
                       {"chunk_size": 64 * 1024 * 1024 + 1}, {"attempts": 0},
                       {"attempts": 11}, {"timeout": 0}, {"timeout": 121},
                       {"retry_delay": -1}, {"retry_delay": 31}):
            with self.subTest(change=change):
                with self.assertRaises(core.RecoveryError):
                    self.run_download(**change)
                self.assertEqual(self.transport.calls, [])

    def test_invalid_expected_digests_reject_before_network(self):
        for digest in ("abc", "g" * 64, "f" * 63, "f" * 65, "sha256:" + "a" * 64):
            with self.subTest(digest=digest):
                with self.assertRaises(core.RecoveryError):
                    self.run_download(expected_sha256=digest)
                self.assertEqual(self.transport.calls, [])


class HTTPTransportTests(unittest.TestCase):
    """Production request construction is tested behind an in-memory opener."""
    def setUp(self):
        self.transport = core.HTTPTransport()
        self.opener = mock.Mock()
        self.transport.opener = self.opener

    def test_request_uses_only_range_identity_and_nonsecret_user_agent(self):
        response = object()
        self.opener.open.return_value = response
        self.assertIs(self.transport.open(SYNTHETIC_URL, 10, 19, SYNTHETIC_TAG, 8), response)
        request = self.opener.open.call_args.args[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(headers["range"], "bytes=10-19")
        self.assertEqual(headers["if-range"], SYNTHETIC_TAG)
        self.assertEqual(headers["accept-encoding"], "identity")
        self.assertEqual(set(headers), {"range", "if-range", "accept-encoding", "user-agent"})
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 8)

    def test_probe_omits_if_range(self):
        self.transport.open(SYNTHETIC_URL, 0, 0, None, 5)
        request = self.opener.open.call_args.args[0]
        self.assertFalse(request.has_header("If-range"))

    def test_transient_http_statuses_are_retryable_and_sanitized(self):
        for code in (408, 429, 500, 502, 503, 504):
            with self.subTest(code=code):
                body = io.BytesIO(b"synthetic error body")
                self.opener.open.side_effect = urllib.error.HTTPError(
                    SYNTHETIC_URL, code, SYNTHETIC_URL, {}, body)
                with self.assertRaises(core.RetryableError) as result:
                    self.transport.open(SYNTHETIC_URL, 0, 0, None, 5)
                self.assertNotIn(SYNTHETIC_URL, str(result.exception))
                self.assertTrue(body.closed)

    def test_terminal_http_statuses_are_sanitized_and_closed(self):
        for code in (400, 401, 403, 404, 416):
            with self.subTest(code=code):
                body = io.BytesIO()
                self.opener.open.side_effect = urllib.error.HTTPError(
                    SYNTHETIC_URL, code, SYNTHETIC_URL, {}, body)
                with self.assertRaises(core.RecoveryError) as result:
                    self.transport.open(SYNTHETIC_URL, 0, 0, None, 5)
                self.assertNotIsInstance(result.exception, core.RetryableError)
                self.assertNotIn(SYNTHETIC_URL, str(result.exception))
                self.assertTrue(body.closed)

    def test_tls_errors_are_terminal_and_sanitized(self):
        for error in (ssl.SSLError(SYNTHETIC_URL),
                      urllib.error.URLError(ssl.SSLError(SYNTHETIC_URL))):
            with self.subTest(error=type(error).__name__):
                self.opener.open.side_effect = error
                with self.assertRaises(core.RecoveryError) as result:
                    self.transport.open(SYNTHETIC_URL, 0, 0, None, 5)
                self.assertNotIsInstance(result.exception, core.RetryableError)
                self.assertNotIn(SYNTHETIC_URL, str(result.exception))

    def test_connection_failures_are_retryable_and_sanitized(self):
        for error in (urllib.error.URLError(SYNTHETIC_URL), OSError(SYNTHETIC_URL),
                      http.client.RemoteDisconnected(SYNTHETIC_URL)):
            with self.subTest(error=type(error).__name__):
                self.opener.open.side_effect = error
                with self.assertRaises(core.RetryableError) as result:
                    self.transport.open(SYNTHETIC_URL, 0, 0, None, 5)
                self.assertNotIn(SYNTHETIC_URL, str(result.exception))

    def test_redirect_cannot_downgrade_or_add_credentials(self):
        handler = core._HTTPSRedirect()
        request = urllib.request.Request(SYNTHETIC_URL, headers={"Range": "bytes=0-0"})
        for destination in ("http://download.invalid/a", "https://user:pass@download.invalid/a",
                            "https://download.invalid/a#fragment"):
            with self.subTest(destination=destination):
                with self.assertRaises(core.RecoveryError):
                    handler.redirect_request(request, None, 302, "Found", {}, destination)

    def test_https_redirect_preserves_range_but_never_adds_cookies(self):
        handler = core._HTTPSRedirect()
        request = urllib.request.Request(SYNTHETIC_URL,
                    headers={"Range": "bytes=10-19", "If-Range": SYNTHETIC_TAG})
        redirected = handler.redirect_request(request, None, 302, "Found", {},
                                              "https://mirror.invalid/export")
        headers = {name.lower(): value for name, value in redirected.header_items()}
        self.assertEqual(headers["range"], "bytes=10-19")
        self.assertEqual(headers["if-range"], SYNTHETIC_TAG)
        self.assertNotIn("cookie", headers)
        self.assertNotIn("authorization", headers)


if __name__ == "__main__":
    unittest.main()
