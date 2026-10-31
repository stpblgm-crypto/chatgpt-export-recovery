"""Shell checkpoint failures with a local synthetic transport; no network."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "SYNTHETIC_ONLY_DO_NOT_LOG"
HELPER = r'''#!/usr/bin/env python3
import argparse, os
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--url-file')
p.add_argument('--start',type=int)
p.add_argument('--end',type=int)
p.add_argument('--output')
p.add_argument('--headers')
a=p.parse_args()
mode=os.environ['FIXTURE_MODE']
Path(os.environ['FIXTURE_CALLS']).open('a').write(str(a.start)+'\n')
status=206; start=a.start; end=min(a.end,11); total=12
body=b'ABCDEFGHIJKL'[start:end+1]
if mode=='status': status=200
if mode=='start': start+=1; end+=1
if mode=='short': body=body[:-1]
if mode=='long': body+=b'X'
if mode=='changed_total' and a.start>4: total=13
if mode=='auth': status=403
if mode!='no_body': Path(a.output).write_bytes(body)
header=f'HTTP/1.1 {status} Fixture\n'
if mode!='no_range': header+=f'Content-Range: bytes {start}-{end}/{total}\n'
Path(a.headers).write_text(header+'\n')
if mode=='auth': raise SystemExit(22)
if mode=='helper_failure': raise SystemExit(23)
'''
SCRIPT = r'''
source "$FIXTURE_REPO/src/lib/download.sh"
source "$FIXTURE_REPO/src/lib/download_cdp.sh"
download_recover_cdp "https://chatgpt.com/synthetic?token=$FIXTURE_SENTINEL" \
  "$FIXTURE_FINAL" "$FIXTURE_WORK" "$FIXTURE_HELPER" "$FIXTURE_CHUNK" 4
'''


class ShellCheckpointNegativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.helper = self.root / "fixture_fetch.py"
        self.helper.write_text(HELPER)
        self.helper.chmod(0o700)
        self.final = self.root / "out.zip"
        self.part = self.root / "out.zip.part"
        self.part.write_bytes(b"ABCD")
        self.work = self.root / "work"
        self.work.mkdir()
        self.calls = self.root / "calls"

    def invoke(self, mode, chunk=4):
        env = os.environ.copy()
        env.update(FIXTURE_MODE=mode, FIXTURE_CALLS=str(self.calls),
                   FIXTURE_REPO=str(ROOT), FIXTURE_FINAL=str(self.final),
                   FIXTURE_WORK=str(self.work), FIXTURE_HELPER=str(self.helper),
                   FIXTURE_SENTINEL=SENTINEL, FIXTURE_CHUNK=str(chunk))
        result = subprocess.run(["bash", "-c", SCRIPT], env=env, capture_output=True,
                                text=True, timeout=10)
        self.assertNotIn(SENTINEL, result.stdout + result.stderr)
        return result.returncode

    def unchanged_rejection(self, mode, code=24):
        self.assertEqual(self.invoke(mode), code)
        self.assertEqual(self.part.read_bytes(), b"ABCD")
        self.assertFalse(self.final.exists())
        self.assertFalse((self.work / "download.segment.current").exists())
        self.assertEqual(self.calls.read_text(), "4\n")

    def test_200_never_appends(self):
        self.unchanged_rejection("status")

    def test_wrong_range_start_never_appends(self):
        self.unchanged_rejection("start")

    def test_missing_range_never_appends(self):
        self.unchanged_rejection("no_range")

    def test_short_body_at_floor_never_appends(self):
        self.unchanged_rejection("short")

    def test_oversized_body_at_floor_never_appends(self):
        self.unchanged_rejection("long")

    def test_missing_body_never_appends(self):
        self.unchanged_rejection("no_body")

    def test_auth_failure_is_terminal_without_retry(self):
        self.unchanged_rejection("auth", 22)

    def test_helper_failure_at_floor_is_terminal(self):
        self.unchanged_rejection("helper_failure", 23)

    def test_total_drift_keeps_only_previously_verified_prefix(self):
        self.assertEqual(self.invoke("changed_total"), 24)
        self.assertEqual(self.part.read_bytes(), b"ABCDEFGH")
        self.assertFalse(self.final.exists())
        self.assertEqual(self.calls.read_text(), "4\n8\n")

    def test_zip_failure_never_publishes_complete_http_body(self):
        self.assertEqual(self.invoke("invalid_zip"), 4)
        self.assertEqual(self.part.read_bytes(), b"ABCDEFGHIJKL")
        self.assertFalse(self.final.exists())

    def test_symlink_output_is_rejected_without_touching_target(self):
        target = self.root / "synthetic_original"
        target.write_bytes(b"ORIGINAL")
        self.final.symlink_to(target)
        self.assertEqual(self.invoke("status"), 2)
        self.assertEqual(target.read_bytes(), b"ORIGINAL")
        self.assertEqual(self.part.read_bytes(), b"ABCD")
        self.assertFalse(self.calls.exists())

    def test_invalid_chunk_is_rejected_before_transport(self):
        self.assertEqual(self.invoke("status", chunk=2), 2)
        self.assertEqual(self.part.read_bytes(), b"ABCD")
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
