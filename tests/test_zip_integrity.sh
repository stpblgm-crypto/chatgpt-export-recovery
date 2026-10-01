#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$REPO_ROOT/src/lib/download.sh"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/chatgpt-zip-test.XXXXXX")"
trap 'rm -rf -- "$TEST_DIR"' EXIT
python3 - "$TEST_DIR" <<'PY'
from pathlib import Path
import sys, zipfile
root = Path(sys.argv[1])
with zipfile.ZipFile(root / 'valid.zip', 'w', compression=zipfile.ZIP_STORED) as archive:
    archive.writestr('synthetic.txt', b'SYNTHETIC_ZIP_MEMBER_PAYLOAD')
data = (root / 'valid.zip').read_bytes()
assert b'SYNTHETIC_ZIP_MEMBER_PAYLOAD' in data
(root / 'bad-crc.zip').write_bytes(data.replace(b'SYNTHETIC_ZIP_MEMBER_PAYLOAD', b'XYNTHETIC_ZIP_MEMBER_PAYLOAD', 1))
(root / 'truncated.zip').write_bytes(data[:20])
PY
# Force only the absence of unzip while keeping every other command available.
command() {
    if [ "${1-}" = -v ] && [ "${2-}" = unzip ]; then return 1; fi
    builtin command "$@"
}
verify_zip_file "$TEST_DIR/valid.zip"
if verify_zip_file "$TEST_DIR/bad-crc.zip"; then printf 'FAIL: bad CRC passed Python ZIP check.\n'; exit 1; fi
if verify_zip_file "$TEST_DIR/truncated.zip"; then printf 'FAIL: truncated ZIP passed Python ZIP check.\n'; exit 1; fi
unset -f command
printf 'PASS: Python ZIP fallback rejects bad CRC and truncation, and accepts a valid archive.\n'
