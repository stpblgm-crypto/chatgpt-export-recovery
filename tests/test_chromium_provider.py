#!/usr/bin/env python3
"""Offline only: all browser paths and cookie rows are synthetic temporary files."""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chromium_cookies", ROOT / "src/browsers/chromium_cookies.py")
provider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider)
SECRET = "SYNTHETIC_COOKIE_MUST_NOT_BE_LOGGED"


class ChromiumTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chatgpt-chromium-test.")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
                                          "CHATGPT_RECOVERY_CHROMIUM_COOKIE_DB": "", "CHATGPT_RECOVERY_CHROMIUM_PROFILE": "",
                                          "CHATGPT_RECOVERY_CHROMIUM_BACKEND": "plaintext", "CHATGPT_RECOVERY_CHROMIUM_PRODUCT": "chromium"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.work = self.home / "chatgpt-export-recovery.fixture"
        self.work.mkdir(mode=0o700)
        self.jar = self.work / "cookies.txt"
        self.meta = self.work / "cookie-meta"
        self.future = (int(time.time()) + 3600 + provider.CHROMIUM_EPOCH_SECONDS) * 1000000

    def row(self, **overrides):
        data = dict(host_key=".chatgpt.com", path="/", name="session-test", value=SECRET,
                    encrypted_value=b"", expires_utc=self.future, is_secure=1, is_httponly=1,
                    is_persistent=1, top_frame_site_key="", has_cross_site_ancestor=0, last_access_utc=100)
        data.update(overrides)
        return tuple(data.values())

    def database(self, relative=".config/chromium/Default/Network/Cookies", rows=None):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE cookies (host_key TEXT, path TEXT, name TEXT, value TEXT, encrypted_value BLOB, expires_utc INTEGER, is_secure INTEGER, is_httponly INTEGER, is_persistent INTEGER, top_frame_site_key TEXT, has_cross_site_ancestor INTEGER, last_access_utc INTEGER)")
            conn.executemany("INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows or [self.row()])
        return path

    def build(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(provider.sys, "argv", ["provider", "build", str(self.jar), str(self.meta), str(self.work)]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = provider.main()
        self.assertNotIn(SECRET, out.getvalue() + err.getvalue())
        return rc, out.getvalue() + err.getvalue()

    def test_empty_detection_never_uses_real_home(self):
        self.assertEqual(provider.candidates(), [])

    def test_bounded_known_layouts_and_modern_precedence(self):
        modern = self.database()
        self.database(".config/chromium/Default/Cookies")
        self.database(".config/chromium/Arbitrary/Nested/Profile 8/Cookies")
        snap = self.database("snap/chromium/common/chromium/Profile 2/Cookies")
        flatpak = self.database(".var/app/org.chromium.Chromium/config/chromium/Default/Cookies")
        self.assertEqual(set(provider.candidates()), {modern, snap, flatpak})

    def test_explicit_override_is_exclusive_and_uri_escaped(self):
        selected = self.database("fixture ? #/Cookies")
        self.database()
        os.environ["CHATGPT_RECOVERY_CHROMIUM_COOKIE_DB"] = str(selected)
        self.assertEqual(provider.candidates(), [selected])
        self.assertEqual(self.build()[0], 0)

    def test_missing_override_does_not_fall_back(self):
        self.database()
        os.environ["CHATGPT_RECOVERY_CHROMIUM_COOKIE_DB"] = str(self.home / "missing")
        self.assertEqual(provider.candidates(), [])
        self.assertEqual(self.build()[0], 3)

    def test_ambiguous_profiles_fail_closed(self):
        self.database()
        self.database(".config/google-chrome/Profile 1/Cookies")
        self.assertEqual(self.build()[0], 3)
        self.assertFalse(self.jar.exists())

    def test_plaintext_scope_expiry_session_and_modes(self):
        database = self.database(rows=[self.row(), self.row(host_key=".openai.com", name="session", is_persistent=0, expires_utc=1),
                                      self.row(host_key="chatgpt.com.attacker.test", value="UNRELATED"),
                                      self.row(host_key="notchatgpt.com", value="UNRELATED"),
                                      self.row(name="expired", expires_utc=1, encrypted_value=b"v20expired")])
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        self.assertEqual(self.build()[0], 0)
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)
        text = self.jar.read_text()
        self.assertIn(SECRET, text)
        self.assertNotIn("UNRELATED", text)
        self.assertNotIn("expired", text)
        self.assertIn("#HttpOnly_.openai.com\tTRUE\t/\tTRUE\t0\tsession\t", text)
        for path in (self.jar, self.meta):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_encrypted_matching_cookie_refuses_partial_jar(self):
        self.database(rows=[self.row(), self.row(name="encrypted-auth", value="", encrypted_value=b"v20" + SECRET.encode())])
        rc, log = self.build()
        self.assertEqual(rc, 3)
        self.assertIn("encrypted Chromium cookies are unsupported", log)
        self.assertFalse(self.jar.exists())

    def test_partitioned_cookies_fail_closed(self):
        self.database(rows=[self.row(top_frame_site_key="https://chatgpt.com")])
        self.assertEqual(self.build()[0], 3)
        self.assertFalse(self.jar.exists())

    def test_unpartitioned_ancestor_bit_is_accepted(self):
        self.database(rows=[self.row(top_frame_site_key="", has_cross_site_ancestor=1)])
        self.assertEqual(self.build()[0], 0)

    def test_control_characters_fail_without_logging_value(self):
        self.database(rows=[self.row(value=SECRET + "\n")])
        self.assertEqual(self.build()[0], 3)
        self.assertFalse(self.jar.exists())

    def test_existing_cookie_output_is_not_overwritten(self):
        self.database()
        self.jar.write_text("existing")
        self.assertEqual(self.build()[0], 3)
        self.assertEqual(self.jar.read_text(), "existing")

    def test_insecure_runtime_fails(self):
        self.database()
        self.work.chmod(0o755)
        self.assertEqual(self.build()[0], 3)
        self.assertFalse(self.jar.exists())

    def test_profile_selects_network_cookie_db(self):
        database = self.database()
        os.environ["CHATGPT_RECOVERY_CHROMIUM_PROFILE"] = str(database.parent.parent)
        self.assertEqual(provider.candidates(), [database])
        self.assertEqual(self.build()[0], 0)

    def test_optional_unknown_encryption_is_rejected_before_dependency(self):
        self.database(rows=[self.row(value="", encrypted_value=b"v20" + SECRET.encode())])
        os.environ["CHATGPT_RECOVERY_CHROMIUM_BACKEND"] = "browser-cookie3"
        with patch.object(provider, "library_values") as library:
            self.assertEqual(self.build()[0], 3)
            library.assert_not_called()

    def test_optional_adapter_gets_only_valid_domains_and_version(self):
        database = self.database(rows=[self.row(value="", encrypted_value=b"v10synthetic"),
                                      self.row(host_key="chatgpt.com.attacker.test", value="UNRELATED")])
        with sqlite3.connect(database) as conn:
            conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
            conn.execute("INSERT INTO meta VALUES ('version', '24')")
        os.environ["CHATGPT_RECOVERY_CHROMIUM_BACKEND"] = "browser-cookie3"
        def fake_library(rows, version, work):
            self.assertEqual(len(rows), 1)
            self.assertEqual(version, 24)
            self.assertEqual(rows[0][0], ".chatgpt.com")
            return {(".chatgpt.com", "/", "session-test"): SECRET}
        with patch.object(provider, "library_values", side_effect=fake_library):
            self.assertEqual(self.build()[0], 0)
        self.assertIn(SECRET, self.jar.read_text())

    def test_optional_adapter_raw_exception_never_logged(self):
        self.database()
        os.environ["CHATGPT_RECOVERY_CHROMIUM_BACKEND"] = "browser-cookie3"
        with patch.object(provider, "library_values", side_effect=RuntimeError(SECRET)):
            self.assertEqual(self.build()[0], 3)
        self.assertFalse(self.jar.exists())

    def test_pinned_library_v10_version24_synthetic_fixture(self):
        try:
            import browser_cookie3
        except ImportError:
            self.skipTest("optional browser-cookie3 is not installed")
        # Generate a synthetic v10 fixture; never contact an OS keyring in tests.
        from Cryptodome.Cipher import AES
        from Cryptodome.Protocol.KDF import PBKDF2
        from Cryptodome.Util.Padding import pad
        payload = hashlib.sha256(b".chatgpt.com").digest() + SECRET.encode()
        key = PBKDF2(b"peanuts", b"saltysalt", 16, 1)
        encrypted = b"v10" + AES.new(key, AES.MODE_CBC, b" " * 16).encrypt(pad(payload, 16))
        database = self.database(rows=[self.row(value="", encrypted_value=encrypted)])
        with sqlite3.connect(database) as conn:
            conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
            conn.execute("INSERT INTO meta VALUES ('version', '24')")
        os.environ["CHATGPT_RECOVERY_CHROMIUM_BACKEND"] = "browser-cookie3"
        with patch.object(browser_cookie3._LinuxPasswordManager, "get_password", return_value=b"synthetic-test-keyring"):
            self.assertEqual(self.build()[0], 0)
        self.assertIn(SECRET, self.jar.read_text())
        self.assertEqual(list(self.work.glob("chromium-filtered.*")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
