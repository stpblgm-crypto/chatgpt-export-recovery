"""Offline checkout contract; never discovers or attaches a browser."""
import os
from pathlib import Path
import stat
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'src/browsers/chromium_cdp_fetch.py'


class CDPHelperPackagingTests(unittest.TestCase):
    def test_helper_has_executable_git_checkout_mode(self):
        self.assertTrue(HELPER.is_file())
        self.assertEqual(stat.S_IMODE(HELPER.stat().st_mode) & stat.S_IXUSR, stat.S_IXUSR)

    def test_helper_passes_existing_cli_executable_guard(self):
        self.assertTrue(os.access(HELPER, os.X_OK))

    def test_direct_help_launch_needs_no_chmod_or_browser(self):
        result = subprocess.run([str(HELPER), '--help'], capture_output=True,
                                text=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--url-file', result.stdout)
        self.assertIn('--start', result.stdout)
        self.assertIn('--end', result.stdout)


if __name__ == '__main__':
    unittest.main()
