#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
TD="$(mktemp -d)"
trap 'rm -rf "$TD"' EXIT

# Stable shared primitives are sourced from the production library in the real
# suite. This test only replaces the transport with a deterministic local
# helper and verifies invalid-final -> .part -> exact resume -> ZIP PASS.
source "$ROOT/src/lib/download.sh"
source "$ROOT/src/lib/download_cdp.sh"

python3 - "$TD/remote.zip" <<'PY'
import sys, zipfile
p=sys.argv[1]
with zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED) as z:
    z.writestr('a.txt','A'*50000)
    z.writestr('b.txt','B'*70000)
PY

REMOTE="$TD/remote.zip"
TOTAL="$(stat -c %s "$REMOTE")"
CUT=$((TOTAL/2))
head -c "$CUT" "$REMOTE" > "$TD/out.zip"

cat > "$TD/fetch.py" <<'PY'
#!/usr/bin/env python3
import argparse, os
p=argparse.ArgumentParser()
p.add_argument('--url-file')
p.add_argument('--start',type=int)
p.add_argument('--end',type=int)
p.add_argument('--output')
p.add_argument('--headers')
a=p.parse_args()
remote=os.environ['REMOTE']
total=os.stat(remote).st_size
end=min(a.end,total-1)
with open(remote,'rb') as f:
    f.seek(a.start)
    data=f.read(end-a.start+1)
open(a.output,'wb').write(data)
open(a.headers,'w').write(
    f'HTTP/1.1 206 OK\nContent-Range: bytes {a.start}-{end}/{total}\n'
    f'Content-Length: {len(data)}\n\n'
)
PY
chmod +x "$TD/fetch.py"
export REMOTE
mkdir "$TD/work"

download_recover_cdp   'https://chatgpt.com/backend-api/estuary/content?token=WITHHELD'   "$TD/out.zip" "$TD/work" "$TD/fetch.py" 4096 1024

cmp "$REMOTE" "$TD/out.zip"
printf 'PASS: CDP engine resumed invalid prefix to exact ZIP and finalized it.\n'
