"""Deterministic TCP framing regressions; no sockets, browser or URL file."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


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


if __name__ == '__main__':
    unittest.main()
