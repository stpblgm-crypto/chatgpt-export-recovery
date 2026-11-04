"""One-time, loopback-only browser input. Never serve files or log export URLs."""

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import queue
import secrets
import socket
import threading
import urllib.parse

from .core import RecoveryError, validate_url


class _InputServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.input_path = "/" + secrets.token_urlsafe(32)
        self.session_code = secrets.token_hex(8)
        self.received = queue.Queue(maxsize=1)
        self.accepted = False
        self.accept_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _Handler)
        self.host_header = f"127.0.0.1:{self.server_port}"
        self.origin = "http://" + self.host_header

    def handle_error(self, request, client_address):
        # Request exceptions must not print input values or request data.
        pass


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        pass

    def _reply(self, status, body):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def _route_allowed(self):
        return (self.headers.get_all("Host") == [self.server.host_header]
                and secrets.compare_digest(self.path, self.server.input_path))

    def _form(self, message=""):
        computer = html.escape(socket.gethostname())
        session = html.escape(self.server.session_code)
        return ("<!doctype html><html><head><meta charset='utf-8'>"
                "<title>ChatGPT Export Recovery</title></head><body>"
                "<h1>Local download link input</h1>"
                f"<p>This computer: {computer}</p><p>Local session: {session}</p>"
                f"<p>{html.escape(message)}</p>"
                "<form method='post'><label>Direct HTTPS download link "
                "<input type='password' name='url' autocomplete='off' maxlength='65536' required>"
                "</label><button type='submit'>Continue locally</button></form>"
                "<p>Copy the actual file link from your browser. The local downloader will check it.</p>"
                "</body></html>")

    def do_GET(self):
        if not self._route_allowed():
            self._reply(404, "Not found")
        elif self.server.accepted:
            self._reply(409, "A link was already accepted for this local session.")
        else:
            self._reply(200, self._form())

    def do_POST(self):
        if not self._route_allowed():
            self._reply(404, "Not found")
            return
        if self.headers.get_all("Origin") != [self.server.origin]:
            self._reply(403, "Submit this form from its local page.")
            return
        if self.headers.get("Transfer-Encoding"):
            self._reply(400, "Unsupported request encoding.")
            return
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            self._reply(400, "A bounded form request is required.")
            return
        size = int(lengths[0])
        if not 0 < size <= 262144:
            self._reply(413, "Form input is too large or empty.")
            return
        if self.headers.get_content_type() != "application/x-www-form-urlencoded":
            self._reply(415, "Use the local link form.")
            return
        try:
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError
            fields = urllib.parse.parse_qs(body.decode("utf-8"), strict_parsing=True,
                                          keep_blank_values=True, max_num_fields=2)
            if set(fields) != {"url"} or len(fields["url"]) != 1 or len(fields["url"][0]) > 65536:
                raise ValueError
            url = validate_url(fields["url"][0].strip())
        except (RecoveryError, ValueError, UnicodeError):
            self._reply(400, self._form("Use one complete HTTPS file download link."))
            return
        with self.server.accept_lock:
            if self.server.accepted:
                self._reply(409, "A link was already accepted for this local session.")
                return
            self.server.accepted = True
        try:
            self._reply(200, "<!doctype html><title>Link accepted locally</title>"
                        "<h1>Link accepted locally</h1><p>Return to the terminal for download progress.</p>")
        finally:
            self.server.received.put_nowait(url)


def _show_address(url, session_code):
    print(f"Local input URL: {url}\nLocal session: {session_code}", flush=True)


def read_browser_url(*, timeout=300, ready=None) -> str:
    """Receive one validated URL in memory, then close the local input listener."""
    if not 0 < timeout <= 600:
        raise RecoveryError("Invalid local input timeout.")
    try:
        server = _InputServer()
    except OSError:
        raise RecoveryError("The local input listener could not be started.") from None
    worker = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.05}, daemon=True)
    worker.start()
    try:
        (ready or _show_address)(server.origin + server.input_path, server.session_code)
        try:
            return server.received.get(timeout=timeout)
        except queue.Empty:
            raise RecoveryError("Local input timed out. No download was started.") from None
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
