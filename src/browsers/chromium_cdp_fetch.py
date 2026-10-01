#!/usr/bin/env python3
"""Fetch one authenticated HTTP Range segment through an existing Chromium CDP target.

This helper never reads, decrypts, exports, or prints browser cookies. It asks the
already-running browser network stack to perform the request with
includeCredentials=true and streams the response body through CDP IO.read.

Intended to be invoked by chatgpt-export-recover. The signed export URL is read
from a mode-0600 file and is never printed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ALLOWED_HOSTS = ("chatgpt.com", "openai.com")
DEFAULT_PROBE_PORTS = (9222, 9223, 9515)


def fail(msg: str, code: int = 2) -> "NoReturn":
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def is_allowed_url(url: str) -> bool:
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if p.scheme != "https" or p.username or p.password or not p.hostname:
        return False
    host = p.hostname.lower()
    return any(host == root or host.endswith("." + root) for root in ALLOWED_HOSTS)


def read_secret_url(path: Path) -> str:
    st = path.stat()
    if st.st_mode & 0o077:
        fail("signed URL file must not be group/world accessible")
    url = path.read_text(encoding="utf-8").splitlines()[0].strip()
    if not is_allowed_url(url):
        fail("signed export URL is not an allowed ChatGPT/OpenAI HTTPS URL")
    return url


def http_json(url: str, timeout: float = 0.8) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "chatgpt-export-recovery/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def candidate_endpoints() -> Iterable[str]:
    seen = set()
    explicit = os.environ.get("CHATGPT_RECOVERY_CDP_ENDPOINT", "").strip()
    if explicit:
        explicit = explicit.rstrip("/")
        if explicit not in seen:
            seen.add(explicit)
            yield explicit

    for proc in Path("/proc").glob("[0-9]*"):
        try:
            raw = (proc / "cmdline").read_bytes().split(b"\0")
            args = [x.decode("utf-8", "ignore") for x in raw if x]
        except Exception:
            continue
        joined = " ".join(args).lower()
        if not any(x in joined for x in ("chrome", "chromium", "brave", "msedge")):
            continue
        port = None
        user_data_dir = None
        for arg in args:
            if arg.startswith("--remote-debugging-port="):
                value = arg.split("=", 1)[1]
                if value.isdigit() and value != "0":
                    port = int(value)
            elif arg.startswith("--user-data-dir="):
                user_data_dir = Path(arg.split("=", 1)[1]).expanduser()
        if port:
            ep = f"http://127.0.0.1:{port}"
            if ep not in seen:
                seen.add(ep)
                yield ep
        if user_data_dir:
            active = user_data_dir / "DevToolsActivePort"
            try:
                lines = active.read_text(encoding="utf-8").splitlines()
                if lines and lines[0].isdigit():
                    ep = f"http://127.0.0.1:{int(lines[0])}"
                    if ep not in seen:
                        seen.add(ep)
                        yield ep
            except Exception:
                pass

    for port in DEFAULT_PROBE_PORTS:
        ep = f"http://127.0.0.1:{port}"
        if ep not in seen:
            seen.add(ep)
            yield ep


def choose_target() -> Tuple[str, Dict[str, Any]]:
    for endpoint in candidate_endpoints():
        try:
            targets = http_json(endpoint + "/json/list")
        except Exception:
            continue
        pages = [x for x in targets if x.get("type") == "page" and x.get("webSocketDebuggerUrl")]
        preferred = []
        for target in pages:
            url = target.get("url", "")
            try:
                host = urllib.parse.urlsplit(url).hostname or ""
            except Exception:
                host = ""
            if any(host == root or host.endswith("." + root) for root in ALLOWED_HOSTS):
                preferred.append(target)
        if preferred:
            return endpoint, preferred[0]
    fail("no reachable Chromium CDP page target on chatgpt.com/openai.com")


class WSClient:
    def __init__(self, ws_url: str, timeout: float = 60.0):
        p = urllib.parse.urlsplit(ws_url)
        if p.scheme != "ws" or p.hostname not in ("127.0.0.1", "localhost", "::1"):
            fail("CDP WebSocket must be local ws://")
        self.sock = socket.create_connection((p.hostname, p.port or 80), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = p.path or "/"
        if p.query:
            path += "?" + p.query
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {p.hostname}:{p.port or 80}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self._recv_until(b"\r\n\r\n", 65536)
        status = response.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            fail("CDP WebSocket handshake failed")
        self._next_id = 1

    def close(self) -> None:
        try:
            self._send_frame(b"", opcode=8)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def _recv_exact(self, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise EOFError("websocket closed")
            out.extend(chunk)
        return bytes(out)

    def _recv_until(self, marker: bytes, limit: int) -> bytes:
        out = bytearray()
        while marker not in out:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise EOFError("connection closed")
            out.extend(chunk)
            if len(out) > limit:
                raise ValueError("handshake too large")
        return bytes(out)

    def _send_frame(self, payload: bytes, opcode: int = 1) -> None:
        first = 0x80 | (opcode & 0x0F)
        length = len(payload)
        if length < 126:
            header = bytes([first, 0x80 | length])
        elif length < (1 << 16):
            header = bytes([first, 0x80 | 126]) + struct.pack("!H", length)
        else:
            header = bytes([first, 0x80 | 127]) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def _recv_message(self) -> bytes:
        parts = bytearray()
        while True:
            h = self._recv_exact(2)
            fin = bool(h[0] & 0x80)
            opcode = h[0] & 0x0F
            masked = bool(h[1] & 0x80)
            length = h[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 8:
                raise EOFError("websocket closed")
            if opcode == 9:
                self._send_frame(payload, opcode=10)
                continue
            if opcode == 10:
                continue
            if opcode in (1, 2, 0):
                parts.extend(payload)
            else:
                continue
            if fin:
                return bytes(parts)

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        request = {"id": msg_id, "method": method}
        if params is not None:
            request["params"] = params
        self._send_frame(json.dumps(request, separators=(",", ":")).encode("utf-8"))
        while True:
            payload = self._recv_message()
            try:
                message = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            if message.get("id") != msg_id:
                continue
            if "error" in message:
                code = message["error"].get("code", "unknown")
                raise RuntimeError(f"CDP command {method} failed (code={code})")
            return message.get("result", {})


def header_value(headers: Dict[str, Any], name: str) -> str:
    lname = name.lower()
    for k, v in headers.items():
        if str(k).lower() == lname:
            return str(v)
    return ""


def write_response_headers(path: Path, status: int, headers: Dict[str, Any]) -> None:
    content_range = header_value(headers, "content-range")
    content_length = header_value(headers, "content-length")
    with path.open("w", encoding="utf-8") as f:
        f.write(f"HTTP/1.1 {status} CDP\n")
        if content_range:
            f.write(f"Content-Range: {content_range}\n")
        if content_length:
            f.write(f"Content-Length: {content_length}\n")
        f.write("\n")
    os.chmod(path, 0o600)


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--url-file", required=True)
    ap.add_argument("--start", required=True, type=int)
    ap.add_argument("--end", required=True, type=int)
    ap.add_argument("--output", required=True)
    ap.add_argument("--headers", required=True)
    ns = ap.parse_args()

    if ns.start < 0 or ns.end < ns.start:
        fail("invalid range")
    secret = Path(ns.url_file)
    output = Path(ns.output)
    headers_path = Path(ns.headers)
    url = read_secret_url(secret)

    endpoint, target = choose_target()
    print(f"cdp_endpoint={endpoint}; target=chatgpt/openai; cookie_export=NO", file=sys.stderr)

    ws = WSClient(target["webSocketDebuggerUrl"], timeout=120.0)
    stream = None
    try:
        ws.call("Network.enable")
        ws.call("Page.enable")
        frame_tree = ws.call("Page.getFrameTree")
        frame_id = frame_tree.get("frameTree", {}).get("frame", {}).get("id")
        if not frame_id:
            fail("could not identify CDP page frame")

        ws.call(
            "Network.setExtraHTTPHeaders",
            {"headers": {
                "Range": f"bytes={ns.start}-{ns.end}",
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                "Referer": "https://chatgpt.com/",
            }},
        )
        result = ws.call(
            "Network.loadNetworkResource",
            {
                "frameId": frame_id,
                "url": url,
                "options": {"disableCache": True, "includeCredentials": True},
            },
        )
        resource = result.get("resource", {})
        status = int(resource.get("httpStatusCode") or 0)
        response_headers = resource.get("headers") or {}
        write_response_headers(headers_path, status, response_headers)
        stream = resource.get("stream")
        if status != 206 or not stream:
            return 22 if status in (401, 403) else 24

        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as out:
            os.chmod(output, 0o600)
            while True:
                chunk = ws.call("IO.read", {"handle": stream, "size": 1048576})
                data = chunk.get("data", "")
                if data:
                    if chunk.get("base64Encoded"):
                        out.write(base64.b64decode(data, validate=True))
                    else:
                        try:
                            out.write(data.encode("latin-1"))
                        except UnicodeEncodeError:
                            fail("CDP returned non-base64 binary data that cannot be preserved")
                if chunk.get("eof"):
                    break
            out.flush()
            os.fsync(out.fileno())
        return 0
    except (OSError, EOFError, TimeoutError, RuntimeError, ValueError) as exc:
        print(f"ERROR: Chromium CDP transport failed: {type(exc).__name__}", file=sys.stderr)
        return 23
    finally:
        if stream:
            try:
                ws.call("IO.close", {"handle": stream})
            except Exception:
                pass
        try:
            ws.call("Network.setExtraHTTPHeaders", {"headers": {}})
        except Exception:
            pass
        ws.close()


if __name__ == "__main__":
    raise SystemExit(main())
