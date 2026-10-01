#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "src/browsers/chromium_cdp_fetch.py"


def recv_exact(sock, n):
    out = bytearray()
    while len(out) < n:
        b = sock.recv(n - len(out))
        if not b:
            raise EOFError
        out.extend(b)
    return bytes(out)


def recv_frame(sock):
    h = recv_exact(sock, 2)
    opcode = h[0] & 0x0F
    n = h[1] & 0x7F
    masked = bool(h[1] & 0x80)
    if n == 126:
        n = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack("!Q", recv_exact(sock, 8))[0]
    mask = recv_exact(sock, 4) if masked else b""
    data = recv_exact(sock, n)
    if masked:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data


def send_text(sock, obj):
    data = json.dumps(obj, separators=(",", ":")).encode()
    if len(data) < 126:
        header = bytes([0x81, len(data)])
    elif len(data) < 65536:
        header = bytes([0x81, 126]) + struct.pack("!H", len(data))
    else:
        header = bytes([0x81, 127]) + struct.pack("!Q", len(data))
    sock.sendall(header + data)


class FakeCDP:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        try:
            req = b""
            while b"\r\n\r\n" not in req:
                req += conn.recv(4096)
            first = req.split(b"\r\n", 1)[0]
            if first.startswith(b"GET /json/list"):
                body = json.dumps([{
                    "type": "page",
                    "url": "https://chatgpt.com/",
                    "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.port}/devtools/page/1",
                }]).encode()
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode() + b"\r\n\r\n" + body
                )
                return
            if first.startswith(b"GET /devtools/page/1"):
                key = None
                for line in req.split(b"\r\n"):
                    if line.lower().startswith(b"sec-websocket-key:"):
                        key = line.split(b":", 1)[1].strip()
                accept = base64.b64encode(
                    hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
                )
                conn.sendall(
                    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                    b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
                )
                while True:
                    opcode, data = recv_frame(conn)
                    if opcode == 8:
                        return
                    if opcode != 1:
                        continue
                    msg = json.loads(data.decode())
                    mid = msg["id"]
                    method = msg["method"]
                    if method in ("Network.enable", "Page.enable", "Network.setExtraHTTPHeaders", "IO.close"):
                        result = {}
                    elif method == "Page.getFrameTree":
                        result = {"frameTree": {"frame": {"id": "frame-1"}}}
                    elif method == "Network.loadNetworkResource":
                        assert msg["params"]["options"]["includeCredentials"] is True
                        assert msg["params"]["url"].startswith("https://chatgpt.com/")
                        result = {"resource": {
                            "httpStatusCode": 206,
                            "headers": {"Content-Range": "bytes 4-7/8", "Content-Length": "4"},
                            "stream": "s1",
                        }}
                    elif method == "IO.read":
                        result = {
                            "data": base64.b64encode(b"EFGH").decode(),
                            "base64Encoded": True,
                            "eof": True,
                        }
                    else:
                        send_text(conn, {"id": mid, "error": {"code": -1, "message": "unexpected"}})
                        continue
                    send_text(conn, {"id": mid, "result": result})
        finally:
            conn.close()

    def close(self):
        self.sock.close()


def main():
    fake = FakeCDP()
    try:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            secret = td / "url.secret"
            secret.write_text("https://chatgpt.com/backend-api/estuary/content?token=WITHHELD\n")
            secret.chmod(0o600)
            out = td / "segment"
            headers = td / "headers"
            env = os.environ.copy()
            env["CHATGPT_RECOVERY_CDP_ENDPOINT"] = f"http://127.0.0.1:{fake.port}"
            r = subprocess.run([
                str(HELPER), "--url-file", str(secret), "--start", "4", "--end", "7",
                "--output", str(out), "--headers", str(headers)
            ], env=env, text=True, capture_output=True, timeout=10)
            if r.returncode != 0:
                raise SystemExit(f"FAIL helper rc={r.returncode}: {r.stderr}")
            if out.read_bytes() != b"EFGH":
                raise SystemExit("FAIL body mismatch")
            ht = headers.read_text()
            if "206" not in ht or "bytes 4-7/8" not in ht:
                raise SystemExit("FAIL headers mismatch")
            if "token=WITHHELD" in (r.stdout + r.stderr):
                raise SystemExit("FAIL signed URL leaked to output")
            print("PASS: Chromium CDP helper fetched one authenticated synthetic Range segment without URL leakage.")
    finally:
        fake.close()


if __name__ == "__main__":
    main()
