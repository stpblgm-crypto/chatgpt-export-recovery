"""Deterministic TCP framing regressions; no sockets, browser or URL file."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


HELPER = Path(__file__).resolve().parents[1] / 'src/browsers/chromium_cdp_fetch.py'
SPEC = importlib.util.spec_from_file_location('cdp_buffer_subject', HELPER)
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)
UPGRADE = b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n'


def frame(value, opcode=1):
    body = json.dumps(value, separators=(',', ':')).encode() if opcode == 1 else value
    assert len(body) < 126
    return bytes([0x80 | opcode, len(body)]) + body


class FakeSocket:
    def __init__(self, chunks):
        self.chunks = [bytearray(chunk) for chunk in chunks]
        self.sent = []
        self.recv_calls = 0

    def settimeout(self, timeout):
        pass

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        self.recv_calls += 1
        while self.chunks and not self.chunks[0]:
            self.chunks.pop(0)
        if not self.chunks:
            return b''
        result = bytes(self.chunks[0][:size])
        del self.chunks[0][:size]
        return result

    def close(self):
        pass


class WebSocketBufferTests(unittest.TestCase):
    def client(self, chunks):
        sock = FakeSocket(chunks)
        with patch.object(SUBJECT.socket, 'create_connection', return_value=sock):
            client = SUBJECT.WSClient('ws://127.0.0.1:9222/owned-fake')
        self.addCleanup(client.close)
        return client, sock

    def test_complete_first_frame_survives_upgrade_coalescing(self):
        event = {'method': 'Network.dataReceived', 'params': {}}
        client, _ = self.client([UPGRADE + frame(event)])
        self.assertEqual(json.loads(client._recv_message()), event)

    def test_partial_first_event_does_not_desynchronize_command_reply(self):
        event = frame({'method': 'Network.dataReceived', 'params': {}})
        reply = frame({'id': 1, 'result': {'ack': True}})
        client, _ = self.client([UPGRADE + event[:3], event[3:] + reply])
        self.assertEqual(client.call('Network.enable'), {'ack': True})

    def test_split_upgrade_delimiter_preserves_following_frame(self):
        event = frame({'method': 'Page.frameStartedLoading'})
        client, _ = self.client([UPGRADE[:-1], UPGRADE[-1:] + event[:1], event[1:]])
        self.assertEqual(json.loads(client._recv_message()),
                         {'method': 'Page.frameStartedLoading'})

    def test_buffered_bytes_are_consumed_in_order_without_another_recv(self):
        client, sock = self.client([UPGRADE + b'ABCDE'])
        self.assertEqual(client._recv_exact(2), b'AB')
        self.assertEqual(client._recv_exact(3), b'CDE')
        self.assertEqual(sock.recv_calls, 1)

    def test_plain_upgrade_and_later_reply_still_work(self):
        client, _ = self.client([UPGRADE, frame({'id': 1, 'result': {'ack': True}})])
        self.assertEqual(client.call('Network.enable'), {'ack': True})

    def test_incomplete_later_frame_still_raises_eof(self):
        client, _ = self.client([UPGRADE, b'\x81'])
        with self.assertRaises(EOFError):
            client._recv_message()

    def test_coalesced_ping_is_preserved_and_answered(self):
        client, sock = self.client([UPGRADE + frame(b'ping', opcode=9),
                                   frame({'id': 1, 'result': {'ack': True}})])
        self.assertEqual(client.call('Network.enable'), {'ack': True})
        self.assertTrue(any(message[0] & 0x0f == 10 for message in sock.sent[1:]))


class WebSocketConstructionCleanupTests(unittest.TestCase):
    """Constructor failures own their fake socket; no real I/O is possible."""
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name in ('socket', 'getaddrinfo'):
            self.stack.enter_context(patch.object(
                SUBJECT.socket, name, side_effect=AssertionError('real network forbidden')))
        self.sock = Mock()
        self.sock.recv.return_value = UPGRADE
        self.connect = self.stack.enter_context(patch.object(
            SUBJECT.socket, 'create_connection', return_value=self.sock))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def construct(self):
        return SUBJECT.WSClient('ws://127.0.0.1:9222/owned-fake', timeout=3.0)

    def fails_and_closes(self, error):
        with self.assertRaises(type(error)) as caught:
            self.construct()
        self.assertIs(caught.exception, error)
        self.sock.close.assert_called_once_with()

    def test_timeout_setup_failure_closes_socket(self):
        error = OSError('mock timeout setup')
        self.sock.settimeout.side_effect = error
        self.fails_and_closes(error)
        self.sock.sendall.assert_not_called()

    def test_handshake_send_failure_closes_socket(self):
        error = OSError('mock send')
        self.sock.sendall.side_effect = error
        self.fails_and_closes(error)
        self.sock.recv.assert_not_called()

    def test_handshake_read_timeout_closes_socket(self):
        error = TimeoutError('mock recv')
        self.sock.recv.side_effect = error
        self.fails_and_closes(error)

    def test_handshake_eof_closes_socket(self):
        self.sock.recv.return_value = b''
        with self.assertRaisesRegex(EOFError, 'connection closed'):
            self.construct()
        self.sock.close.assert_called_once_with()

    def test_oversized_handshake_closes_socket(self):
        self.sock.recv.return_value = b'X' * 4096
        with self.assertRaisesRegex(ValueError, 'handshake too large'):
            self.construct()
        self.sock.close.assert_called_once_with()

    def test_rejected_handshake_closes_without_sending_close_frame(self):
        self.sock.recv.return_value = b'HTTP/1.1 403 Forbidden\r\n\r\n'
        with self.assertRaises(SystemExit) as caught:
            self.construct()
        self.assertEqual(caught.exception.code, 2)
        self.sock.close.assert_called_once_with()
        self.assertEqual(self.sock.sendall.call_count, 1)

    def test_interrupted_handshake_closes_and_preserves_interrupt(self):
        error = KeyboardInterrupt('mock interrupt')
        self.sock.recv.side_effect = error
        self.fails_and_closes(error)

    def test_cleanup_failure_does_not_replace_original_error(self):
        for cleanup_error in (OSError('mock close'), KeyboardInterrupt('mock close interrupt')):
            with self.subTest(cleanup_error=type(cleanup_error).__name__):
                self.sock.reset_mock()
                original = EOFError('original handshake failure')
                self.sock.recv.side_effect = original
                self.sock.close.side_effect = cleanup_error
                self.fails_and_closes(original)

    def test_connection_failure_does_not_attempt_close(self):
        error = OSError('mock connect')
        self.connect.side_effect = error
        with self.assertRaises(OSError) as caught:
            self.construct()
        self.assertIs(caught.exception, error)
        self.sock.close.assert_not_called()

    def test_success_keeps_socket_open_with_unchanged_request_and_buffer(self):
        self.sock.recv.return_value = UPGRADE + b'AB'
        client = self.construct()
        self.sock.close.assert_not_called()
        self.connect.assert_called_once_with(('127.0.0.1', 9222), timeout=3.0)
        self.sock.settimeout.assert_called_once_with(3.0)
        self.assertEqual(client._next_id, 1)
        self.assertEqual(client._recv_exact(2), b'AB')
        self.assertEqual(self.sock.recv.call_count, 1)
        request = self.sock.sendall.call_args.args[0]
        self.assertTrue(request.startswith(b'GET /owned-fake HTTP/1.1\r\n'))
        self.assertIn(b'Host: 127.0.0.1:9222\r\n', request)
        client.close()
        self.sock.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
