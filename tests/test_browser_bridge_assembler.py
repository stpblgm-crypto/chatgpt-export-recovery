#!/usr/bin/env python3
import hashlib, os, subprocess, tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
assembler=root/"browser_bridge"/"assemble_from_prefix_and_chunks.py"
with tempfile.TemporaryDirectory() as td:
    td=Path(td)
    prefix=td/"prefix.bin"; prefix.write_bytes(b"abcdefgh")
    chunks=td/"chunks"; chunks.mkdir()
    (chunks/"chunk_0000_000000000008_000000000011.bin").write_bytes(b"ijkl")
    (chunks/"chunk_0001_000000000012_000000000015.bin").write_bytes(b"mnop")
    out=td/"out.zip"
    manifest=td/"manifest.json"
    sha=hashlib.sha256(prefix.read_bytes()).hexdigest()
    cp=subprocess.run(["python3",str(assembler),"--prefix",str(prefix),"--prefix-size","8","--prefix-sha256",sha,"--chunks",str(chunks),"--remote-total","16","--output",str(out),"--manifest",str(manifest)],capture_output=True,text=True)
    assert cp.returncode==4, cp.stderr+cp.stdout
    assert out.read_bytes()==b"abcdefghijklmnop"
    assert manifest.exists()
print("PASS: prefix+chunk exact concatenation and gap geometry")
