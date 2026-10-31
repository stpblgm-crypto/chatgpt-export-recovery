#!/usr/bin/env bash
# Complete offline source profile. The existing trusted controller supplies the
# clean environment, read-only source mount and network-none sandbox.
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_ROOT"
export PYTHONDONTWRITEBYTECODE=1

python3 - <<'PY'
from pathlib import Path
import json
import sys
import unittest

def audit(event, args):
    if event in {"socket.__new__", "socket.connect", "socket.bind", "socket.getaddrinfo"}:
        raise RuntimeError("offline unittest profile forbids actual sockets and DNS")

sys.addaudithook(audit)
suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
print("OFFLINE_UNITTEST_RESULT=" + json.dumps({"tests": result.testsRun,
    "failures": len(result.failures), "errors": len(result.errors),
    "skipped": len(result.skipped)}, sort_keys=True))
if not result.wasSuccessful() or not result.testsRun or result.skipped:
    raise SystemExit(1)
for folder in ("chatgpt_export_recovery", "src", "tests"):
    for path in Path(folder).rglob("*.py"):
        compile(path.read_bytes(), str(path), "exec")
compile(Path("launch.pyw").read_bytes(), "launch.pyw", "exec")
print("PASS: all Python sources compile without writing bytecode")
PY

shell_suites=(test_shell_syntax.sh test_range_validation.sh test_resume_logic.sh
              test_no_secret_output.sh test_download_cdp_resume.sh)
for suite in "${shell_suites[@]}"; do
    printf '\nRUN_SHELL_SUITE=%s\n' "$suite"
    bash "$REPO_ROOT/tests/$suite"
done

printf '\nRUN_SMOKE=owned_loopback_cdp_only\n'
python3 "$REPO_ROOT/tests/test_chromium_cdp_transport.py"
python3 -m chatgpt_export_recovery --help >/dev/null
python3 -m chatgpt_export_recovery --version
printf '\nPASS: complete offline Python suite, %s shell suites, owned synthetic CDP smoke, compile and CLI smoke\n' "${#shell_suites[@]}"
