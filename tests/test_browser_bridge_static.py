#!/usr/bin/env python3
from pathlib import Path
import re, subprocess

root=Path(__file__).resolve().parents[1]
ext=root/"browser_bridge"/"opera_range_extension"
bg=(ext/"background.js").read_text()
manifest=(ext/"manifest.json").read_text()
assert "backend-api/estuary/content" in bg
assert 'headers:' in bg and 'Range' in bg
assert "discoverExportUrl" in bg
assert "signed" not in manifest.lower() or "signed" not in manifest
assert "__SIGNED_URL__" not in bg
cp=subprocess.run(["node","--check",str(ext/"background.js")],capture_output=True,text=True)
assert cp.returncode==0,cp.stderr
print("PASS: Opera range bridge static checks")
