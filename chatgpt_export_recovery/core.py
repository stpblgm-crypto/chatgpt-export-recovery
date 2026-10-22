"""Bounded Range downloader. No browser access, cookie jar, or URL persistence."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Callable


class RecoveryError(Exception):
    """A safe, user-facing error (never include remote input or exception text)."""


class Cancelled(RecoveryError):
    pass


class RetryableError(RecoveryError):
    pass


@dataclass(frozen=True)
class Progress:
    phase: str
    completed: int = 0
    total: int = 0
    message: str = ""


@dataclass(frozen=True)
class Result:
    output: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class Config:
    url: str = field(repr=False)
    output: Path
    import_partial: Path | None = None
    expected_sha256: str = ""
    chunk_size: int = 8 * 1024 * 1024
    attempts: int = 4
    timeout: float = 20
    retry_delay: float = 1


def validate_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or not parsed.port in (None, 443)):
            raise ValueError
        if any(ord(c) < 33 or ord(c) > 126 for c in url):
            raise ValueError
    except (ValueError, TypeError):
        raise RecoveryError("Use a complete HTTPS download link without credentials, spaces, or a fragment.") from None
    return url


class _HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        # Range and If-Range are forwarded; no Cookie/Authorization is ever supplied.
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HTTPTransport:
    """The production transport only accepts certificate-verified HTTPS."""

    def __init__(self):
        self.opener = urllib.request.build_opener(_HTTPSRedirect())

    def open(self, url: str, start: int, end: int, etag: str | None, timeout: float):
        headers = {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity",
                   "User-Agent": "ChatGPT-Export-Recovery/0.2"}
        if etag:
            headers["If-Range"] = etag
        try:
            request = urllib.request.Request(validate_url(url), headers=headers)
            return self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if code in (408, 429, 500, 502, 503, 504):
                raise RetryableError("The server is temporarily unavailable.") from None
            if code in (401, 403):
                raise RecoveryError("The link expired or needs a browser session. Obtain a fresh direct download link in your browser.") from None
            if code == 416:
                raise RecoveryError("The server rejected the requested byte range. Check that the link points to the same export.") from None
            raise RecoveryError(f"The server returned HTTP {code}.") from None
        except (ssl.SSLError, ValueError):
            raise RecoveryError("A secure HTTPS connection could not be established.") from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                raise RecoveryError("Certificate verification failed. No security checks were disabled.") from None
            raise RetryableError("Network connection failed.") from None
        except (OSError, http.client.HTTPException):
            raise RetryableError("Network connection failed.") from None


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise Cancelled("Paused. Verified checkpoint bytes are saved; use Start / resume to continue.")


def _regular(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.stat().st_mode)):
        raise RecoveryError("A selected file is not a regular file. Choose a private local folder.")


def _digest_file(path: Path, cancel: threading.Event, count: int | None = None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = count
        while remaining is None or remaining > 0:
            _check_cancel(cancel)
            data = stream.read(min(1024 * 1024, remaining) if remaining is not None else 1024 * 1024)
            if not data:
                break
            digest.update(data)
            if remaining is not None:
                remaining -= len(data)
        if remaining not in (None, 0):
            raise RecoveryError("The checkpoint is shorter than its committed size.")
    return digest


class _OutputLock:
    """OS advisory lock, automatically released after a crash; never delete its inode."""

    def __init__(self, path: Path):
        _regular(path)
        self.file = path.open("a+b")
        self.locked = False
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0, 2)
                if not self.file.tell():
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.locked = True
        except OSError:
            self.file.close()
            raise RecoveryError("Another recovery is using this output. Pause it or choose another output.") from None

    def close(self):
        self.file.close()


def _save_state(path: Path, state: dict) -> None:
    fd, name = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Downloader:
    def __init__(self, config: Config, *, transport=None,
                 cancel: threading.Event | None = None,
                 progress: Callable[[Progress], None] | None = None):
        self.config = config
        self.transport = transport or HTTPTransport()
        self.cancel = cancel or threading.Event()
        self.progress = progress or (lambda event: None)

    def _emit(self, phase: str, done=0, total=0, message=""):
        self.progress(Progress(phase, done, total, message))

    def _segment(self, start: int, end: int, etag=None, total=None):
        for attempt in range(self.config.attempts):
            _check_cancel(self.cancel)
            try:
                with self.transport.open(self.config.url, start, end, etag, self.config.timeout) as response:
                    if response.status != 206:
                        raise RecoveryError("The server did not return HTTP 206. Nothing was appended; use a direct Range-capable download link.")
                    value = response.headers.get("Content-Range", "")
                    match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", value)
                    if not match:
                        raise RecoveryError("The server returned an invalid Content-Range.")
                    first, last, size = map(int, match.groups())
                    if (size <= 0 or first != start or last != min(end, size - 1)
                            or last < first or (total is not None and size != total)):
                        raise RecoveryError("The remote range or archive size changed. The checkpoint was preserved.")
                    tag = response.headers.get("ETag", "")
                    if not re.fullmatch(r'"[\x21\x23-\x7e]*"', tag):
                        raise RecoveryError("The server does not provide a strong ETag. Safe multi-request recovery requires a direct download endpoint with a strong ETag.")
                    if etag is not None and tag != etag:
                        raise RecoveryError("The remote archive changed. The checkpoint was preserved.")
                    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise RecoveryError("The server compressed the byte range; recovery stopped safely.")
                    length = last - first + 1
                    declared = response.headers.get("Content-Length")
                    if declared is not None and (not re.fullmatch("[0-9]+", declared) or int(declared) != length):
                        raise RecoveryError("The server returned an inconsistent Content-Length.")
                    data = bytearray()
                    while len(data) <= length:
                        _check_cancel(self.cancel)
                        block = response.read(min(64 * 1024, length + 1 - len(data)))
                        if not block:
                            break
                        data.extend(block)
                    if len(data) > length:
                        raise RecoveryError("The range body exceeded its declared size.")
                    if len(data) != length:
                        raise RetryableError("The range body was interrupted.")
                    return bytes(data), size, tag
            except (OSError, http.client.HTTPException):
                failure = RetryableError("Network read was interrupted.")
            except RetryableError as error:
                failure = error
            if attempt + 1 >= self.config.attempts:
                raise RecoveryError("Download stopped after bounded retries. Verified bytes are saved; try again later.") from None
            self._emit("retry", start, total or 0, f"Retry {attempt + 1}/{self.config.attempts - 1}: {failure}")
            if self.cancel.wait(min(self.config.retry_delay * 2 ** attempt, 30)):
                _check_cancel(self.cancel)
        raise AssertionError("unreachable")

    def run(self) -> Result:
        """Errors are sanitized at the API boundary; underlying URLs never escape."""
        try:
            return self._run()
        except RecoveryError:
            raise
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            raise RecoveryError("Local storage or checkpoint validation failed. Check the selected folder and available space; existing files are preserved.") from None

    def _run(self) -> Result:
        cfg = self.config
        validate_url(cfg.url)
        if not (1024 <= cfg.chunk_size <= 64 * 1024 * 1024 and 1 <= cfg.attempts <= 10
                and 0 < cfg.timeout <= 120 and 0 <= cfg.retry_delay <= 30):
            raise RecoveryError("Invalid download settings.")
        expected = cfg.expected_sha256.strip().lower()
        if expected and not re.fullmatch("[0-9a-f]{64}", expected):
            raise RecoveryError("Expected SHA256 must contain exactly 64 hexadecimal characters.")
        _check_cancel(self.cancel)
        output = Path(cfg.output).expanduser().absolute()
        if not output.parent.is_dir() or output.is_symlink() or output.exists():
            raise RecoveryError("Output already exists or its folder is unavailable. Choose a new ZIP filename; existing files are never overwritten.")
        workspace = output.with_name(output.name + ".recovery")
        if workspace.is_symlink():
            raise RecoveryError("The recovery folder must not be a symbolic link.")
        workspace.mkdir(mode=0o700, exist_ok=True)
        lock = _OutputLock(workspace / "lock")
        try:
            return self._download(output, workspace, expected)
        finally:
            lock.close()

    def _download(self, output: Path, workspace: Path, expected: str) -> Result:
        part = workspace / "checkpoint.part"
        metadata = workspace / "state.json"
        _regular(part)
        _regular(metadata)
        self._emit("connecting", message="Checking the direct download link…")
        _, total, etag = self._segment(0, 0)
        tag_hash = hashlib.sha256(etag.encode("ascii")).hexdigest()
        location = urllib.parse.urlsplit(self.config.url)
        resource_hash = hashlib.sha256((location.netloc.lower() + location.path).encode("ascii")).hexdigest()
        offset = 0
        digest = hashlib.sha256()
        if metadata.exists():
            if metadata.stat().st_size > 4096 or not part.exists():
                raise RecoveryError("Checkpoint metadata is invalid or its data file is missing.")
            state = json.loads(metadata.read_text(encoding="utf-8"))
            if (not isinstance(state, dict) or state.get("version") != 1 or state.get("total") != total
                    or state.get("etag_sha256") != tag_hash or state.get("resource_sha256") != resource_hash):
                raise RecoveryError("This link does not identify the saved export. Choose the same export or a new output filename.")
            offset = state["committed"]
            if type(offset) is not int or not 0 <= offset <= total or part.stat().st_size < offset:
                raise RecoveryError("Checkpoint size is inconsistent with its metadata.")
            self._emit("checking", offset, total, "Verifying saved checkpoint…")
            digest = _digest_file(part, self.cancel, offset)
            if digest.hexdigest() != state.get("sha256"):
                raise RecoveryError("Checkpoint integrity failed. No bytes were changed; restore a backup or use a new output filename.")
            if part.stat().st_size > offset:
                # A crash may have occurred after data fsync but before metadata commit.
                # Only our uncommitted suffix is discarded, after validating the prefix.
                with part.open("r+b") as stream:
                    stream.truncate(offset)
                    stream.flush()
                    os.fsync(stream.fileno())
        elif part.exists():
            raise RecoveryError("A checkpoint without metadata exists. Import it into a new output to verify and recover it.")
        else:
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            self._commit(metadata, offset, total, tag_hash, resource_hash, digest)
        source = None
        if self.config.import_partial is not None and offset == 0:
            source_path = Path(self.config.import_partial).expanduser().absolute()
            _regular(source_path)
            if source_path == part or source_path == output or not source_path.is_file():
                raise RecoveryError("Choose an existing browser partial file separate from the output.")
            source = source_path.open("rb")
        try:
            source_size = os.fstat(source.fileno()).st_size if source else 0
            if source_size > total:
                raise RecoveryError("The browser partial is larger than this export. Original file preserved.")
            with part.open("r+b") as stream:
                stream.seek(offset)
                while offset < total:
                    _check_cancel(self.cancel)
                    if shutil.disk_usage(workspace).free < min(self.config.chunk_size, total - offset) + 1024 * 1024:
                        raise RecoveryError("Not enough free disk space for the next segment. Free space and resume.")
                    end = min(offset + self.config.chunk_size, total) - 1
                    if source and offset < source_size:
                        end = min(end, source_size - 1)
                    data, _, _ = self._segment(offset, end, etag, total)
                    if source and offset < source_size:
                        if source.read(len(data)) != data:
                            raise RecoveryError("The selected browser partial does not match this export. The original file is unchanged.")
                        phase = "importing"
                    else:
                        phase = "downloading"
                    _check_cancel(self.cancel)
                    if os.fstat(stream.fileno()).st_size != offset:
                        raise RecoveryError("The checkpoint changed while downloading. Recovery stopped.")
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    digest.update(data)
                    offset += len(data)
                    self._commit(metadata, offset, total, tag_hash, resource_hash, digest)
                    self._emit(phase, offset, total, "Verified bytes saved")
        finally:
            if source:
                source.close()
        self._emit("verifying", total, total, "Checking every ZIP entry and computing SHA256…")
        _check_cancel(self.cancel)
        actual = _digest_file(part, self.cancel).hexdigest()
        if actual != digest.hexdigest() or (expected and actual != expected):
            raise RecoveryError("SHA256 verification failed. The checkpoint is retained; no final ZIP was published.")
        self._verify_zip(part)
        _check_cancel(self.cancel)
        # Hard-link is atomic and fails if output already exists, including on Windows.
        # Do not fall back to a POSIX rename that could overwrite a concurrent file.
        try:
            os.link(part, output)
        except FileExistsError:
            raise RecoveryError("Output appeared during download. It was not overwritten; choose a new output filename.") from None
        except OSError:
            raise RecoveryError("This folder cannot atomically publish the ZIP. Check folder permissions or use a local NTFS/APFS/ext4 folder; the verified checkpoint is retained.") from None
        # Keep the checkpoint and metadata as a verified recovery copy. The hard-link
        # consumes no second payload allocation. Never risk reporting failure after publish.
        self._emit("complete", total, total, "ZIP verified and saved")
        return Result(output, total, actual)

    @staticmethod
    def _commit(metadata, offset, total, tag_hash, resource_hash, digest):
        _save_state(metadata, {"version": 1, "committed": offset, "total": total,
                               "etag_sha256": tag_hash, "resource_sha256": resource_hash, "sha256": digest.hexdigest()})

    def _verify_zip(self, part: Path):
        try:
            with zipfile.ZipFile(part) as archive:
                if not archive.infolist():
                    raise RecoveryError("The download is an empty ZIP, not a ChatGPT export.")
                for entry in archive.infolist():
                    _check_cancel(self.cancel)
                    with archive.open(entry) as stream:
                        while stream.read(1024 * 1024):
                            _check_cancel(self.cancel)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error):
            raise RecoveryError("ZIP integrity verification failed. The checkpoint is retained; nothing was extracted or published.") from None
