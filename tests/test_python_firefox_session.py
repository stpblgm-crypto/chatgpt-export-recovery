"""Use synthetic Firefox databases and HTTPS request objects; no live cookies."""

import contextlib
import hashlib
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.request

from chatgpt_export_recovery.core import RecoveryError
from chatgpt_export_recovery.core import Result
from chatgpt_export_recovery import __main__ as app
from chatgpt_export_recovery.firefox_session import FirefoxSessionTransport, _ChatGPTCookiePolicy, _load_jar


@unittest.skipUnless(sys.platform == 'linux', 'Local Firefox authentication is Linux only')
class FirefoxSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.profile = Path(self.temporary.name)
        (self.profile / 'compatibility.ini').write_text('[Compatibility]\nLastVersion=155.0_20261101/20261101\n')
        self.database = self.profile / 'cookies.sqlite'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE moz_cookies (host TEXT, path TEXT, name TEXT, value TEXT, expiry INTEGER, isSecure INTEGER, isHttpOnly INTEGER, originAttributes TEXT, lastAccessed INTEGER)')
        self.secret = 'synthetic-session-never-log'
        self.add('.chatgpt.com', '/', '__Secure-next-auth.session-token', self.secret)

    def tearDown(self):
        self.temporary.cleanup()

    def add(self, host, path, name, value, expiry=None, origin='', accessed=1):
        with sqlite3.connect(self.database) as db:
            db.execute('INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?,?,?)',
                       (host, path, name, value, int(time.time()) + 3600 if expiry is None else expiry,
                        1, 1, origin, accessed))

    def header(self, jar, url):
        request = urllib.request.Request(url)
        jar.add_cookie_header(request)
        return request.get_header('Cookie', '')

    def test_only_first_party_values_are_loaded_and_profile_is_unchanged(self):
        self.add('.other.invalid', '/', 'secret', 'unrelated-cookie-never-load')
        self.add('.openai.com', '/', 'auth', 'different-site-never-load')
        self.add('.chatgpt.com', '/', 'partition', 'container-never-load', origin='^userContextId=1')
        before = hashlib.sha256(self.database.read_bytes()).digest()
        jar = _load_jar(self.profile)
        self.assertEqual([(cookie.name, cookie.value) for cookie in jar],
                         [('__Secure-next-auth.session-token', self.secret)])
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).digest(), before)
        self.assertEqual(set(p.name for p in self.profile.iterdir()), {'compatibility.ini', 'cookies.sqlite'})

    def test_credentials_only_go_to_https_chatgpt_default_port(self):
        jar = _load_jar(self.profile)
        self.assertIn(self.secret, self.header(jar, 'https://chatgpt.com/backend-api/estuary/content'))
        for url in ('http://chatgpt.com/', 'https://chatgpt.com:444/', 'https://files.chatgpt.com/',
                    'https://chatgpt.com.attacker.invalid/', 'https://openai.com/', 'https://storage.invalid/export.zip'):
            with self.subTest(url=url):
                self.assertEqual(self.header(jar, url), '')

    def test_host_only_and_path_scope_are_preserved(self):
        with sqlite3.connect(self.database) as db:
            db.execute('DELETE FROM moz_cookies')
        self.add('chatgpt.com', '/backend-api', 'session', self.secret)
        jar = _load_jar(self.profile)
        cookie = next(iter(jar))
        self.assertFalse(cookie.domain_specified)
        self.assertEqual(self.header(jar, 'https://chatgpt.com/'), '')
        self.assertIn(self.secret, self.header(jar, 'https://chatgpt.com/backend-api/export'))

    def test_expired_values_and_partitioned_sessions_are_not_used(self):
        with sqlite3.connect(self.database) as db:
            db.execute('DELETE FROM moz_cookies')
        self.add('.chatgpt.com', '/', 'session', 'expired', expiry=1)
        self.add('.chatgpt.com', '/', 'session', 'partitioned', origin='^partitionKey=site')
        with self.assertRaisesRegex(RecoveryError, 'No unexpired first-party'):
            _load_jar(self.profile)

    def test_duplicate_cookie_uses_latest_access_without_combining_partitions(self):
        self.add('.chatgpt.com', '/', '__Secure-next-auth.session-token', 'newest', accessed=20)
        self.add('.chatgpt.com', '/', '__Secure-next-auth.session-token', 'container', origin='^userContextId=2', accessed=30)
        jar = _load_jar(self.profile)
        self.assertEqual(len(list(jar)), 1)
        self.assertEqual(next(iter(jar)).value, 'newest')

    def test_session_cookie_without_expiry_is_kept_only_in_memory(self):
        self.add('chatgpt.com', '/', 'session-local', 'session-value', expiry=0)
        cookie = next(c for c in _load_jar(self.profile) if c.name == 'session-local')
        self.assertIsNone(cookie.expires)
        self.assertTrue(cookie.discard)

    def test_invalid_cookie_fields_fail_without_exposing_values(self):
        for value in ('private\nheader-injection', 'private;session=other'):
            with self.subTest(value=value):
                self.add('chatgpt.com', '/', 'session-invalid', value)
                with self.assertRaises(RecoveryError) as error:
                    _load_jar(self.profile)
                self.assertNotIn(value, str(error.exception))

    def test_read_only_sqlite_sees_live_wal_without_copying_it(self):
        with sqlite3.connect(self.database) as live:
            live.execute('PRAGMA journal_mode=WAL')
            live.execute("UPDATE moz_cookies SET value='wal-session'")
            live.commit()
            jar = _load_jar(self.profile)
            self.assertEqual(next(iter(jar)).value, 'wal-session')
            self.assertFalse(any('snapshot' in p.name or 'cookie-jar' in p.name for p in self.profile.iterdir()))

    def test_missing_or_unsupported_database_fails_closed(self):
        missing = self.profile / 'missing'
        with self.assertRaises(RecoveryError):
            _load_jar(missing)
        with sqlite3.connect(self.database) as db:
            db.execute('DROP TABLE moz_cookies')
            db.execute('CREATE TABLE moz_cookies (value TEXT)')
        with self.assertRaisesRegex(RecoveryError, 'schema is unsupported'):
            _load_jar(self.profile)

    def test_symlink_database_is_rejected(self):
        original = self.profile / 'original.sqlite'
        self.database.rename(original)
        self.database.symlink_to(original)
        with self.assertRaisesRegex(RecoveryError, 'regular cookies.sqlite'):
            _load_jar(self.profile)

    def test_transport_close_clears_session_and_does_not_print_it(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with FirefoxSessionTransport(self.profile) as transport:
                self.assertGreater(len(list(transport.jar)), 0)
                handler = next(h for h in transport.opener.handlers if hasattr(h, 'user_agent'))
                req = urllib.request.Request('https://chatgpt.com/')
                req = handler.https_request(req)
                self.assertIn('Firefox/155.0', req.get_header('User-agent'))
            self.assertEqual(len(list(transport.jar)), 0)
        self.assertNotIn(self.secret, output.getvalue())

    def test_redirect_recalculates_cookie_and_never_forwards_it_to_storage(self):
        with FirefoxSessionTransport(self.profile) as transport:
            request = urllib.request.Request('https://chatgpt.com/export')
            transport.jar.add_cookie_header(request)
            self.assertIn(self.secret, request.get_header('Cookie'))
            redirect = next(h for h in transport.opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
            next_request = redirect.redirect_request(request, None, 302, 'Found', {}, 'https://storage.invalid/export.zip')
            transport.jar.add_cookie_header(next_request)
            self.assertIsNone(next_request.get_header('Cookie'))
            with self.assertRaises(RecoveryError):
                redirect.redirect_request(request, None, 302, 'Found', {}, 'http://storage.invalid/export.zip')

    def test_response_cookies_are_never_adopted(self):
        self.assertFalse(_ChatGPTCookiePolicy().set_ok(None, None))

    def test_wrong_initial_host_and_non_linux_fail_before_network(self):
        with FirefoxSessionTransport(self.profile) as transport:
            with patch.object(transport.opener, 'open') as network:
                with self.assertRaises(RecoveryError):
                    transport.open('https://attacker.invalid/', 0, 0, None, 1)
                network.assert_not_called()
        with patch('chatgpt_export_recovery.firefox_session.sys.platform', 'win32'):
            with self.assertRaisesRegex(RecoveryError, 'Linux only'):
                FirefoxSessionTransport(self.profile)


class FirefoxCLITests(unittest.TestCase):
    def test_segment_size_is_explicit_and_bounded(self):
        with patch('chatgpt_export_recovery.local_input.read_browser_url', return_value='https://chatgpt.com/export'), \
                patch.object(app, 'Downloader') as downloader, contextlib.redirect_stdout(io.StringIO()):
            downloader.return_value.run.return_value = Result(Path('export.zip'), 128, 'a' * 64)
            self.assertEqual(app.main(['--cli', '--url-from-browser', '--chunk-mib', '64', '--output', 'export.zip']), 0)
            self.assertEqual(downloader.call_args.args[0].chunk_size, 64 * 1024 * 1024)
        with contextlib.redirect_stderr(io.StringIO()):
            for args in (['--chunk-mib', '64'], ['--cli', '--chunk-mib', '128', '--output', 'export.zip']):
                with self.assertRaises(SystemExit) as error:
                    app.main(args)
                self.assertEqual(error.exception.code, 2)

    def test_profile_requires_cli(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                app.main(['--firefox-profile', '/synthetic/profile'])
        self.assertEqual(error.exception.code, 2)

    def test_explicit_session_is_closed_and_link_is_not_logged(self):
        secret = 'https://chatgpt.com/export?signature=synthetic-never-log'
        output = io.StringIO()
        with patch('chatgpt_export_recovery.local_input.read_browser_url', return_value=secret), \
                patch('chatgpt_export_recovery.firefox_session.FirefoxSessionTransport') as factory, \
                patch.object(app, 'Downloader') as downloader, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            downloader.return_value.run.return_value = Result(Path('export.zip'), 128, 'a' * 64)
            self.assertEqual(app.main(['--cli', '--url-from-browser', '--firefox-profile', '/synthetic/profile',
                                       '--output', 'export.zip']), 0)
            factory.assert_called_once_with(Path('/synthetic/profile'))
            self.assertIs(downloader.call_args.kwargs['transport'], factory.return_value.__enter__.return_value)
            factory.return_value.__exit__.assert_called_once()
        self.assertNotIn(secret, output.getvalue())

    def test_unavailable_session_stops_before_network(self):
        with patch('chatgpt_export_recovery.local_input.read_browser_url', return_value='https://chatgpt.com/export'), \
                patch('chatgpt_export_recovery.firefox_session.FirefoxSessionTransport', side_effect=RecoveryError('Session unavailable.')), \
                patch.object(app, 'Downloader') as downloader, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(['--cli', '--url-from-browser', '--firefox-profile', '/synthetic/profile',
                                       '--output', 'export.zip']), 1)
            downloader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
