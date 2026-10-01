#!/usr/bin/env python3
import argparse, hashlib, json, os, re, sys, zipfile
from pathlib import Path

CHUNK_RE = re.compile(r"^chunk_(\d+)_(\d+)_(\d+)\.bin$")

def sha256_file(path, limit=None):
    h=hashlib.sha256()
    remain=limit
    with open(path,"rb") as f:
        while True:
            if remain is not None and remain <= 0: break
            n=16*1024*1024 if remain is None else min(16*1024*1024,remain)
            b=f.read(n)
            if not b: break
            h.update(b)
            if remain is not None: remain -= len(b)
    return h.hexdigest()

def inventory(chunk_dir):
    rows=[]
    for p in Path(chunk_dir).iterdir():
        m=CHUNK_RE.match(p.name)
        if not m: continue
        idx,start,end=map(int,m.groups())
        size=p.stat().st_size
        expected=end-start+1
        if size != expected:
            raise SystemExit(f"chunk size mismatch: {p.name} got={size} expected={expected}")
        rows.append((start,end,idx,p))
    rows.sort()
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--prefix",required=True)
    ap.add_argument("--prefix-size",required=True,type=int)
    ap.add_argument("--prefix-sha256",required=True)
    ap.add_argument("--chunks",required=True)
    ap.add_argument("--remote-total",required=True,type=int)
    ap.add_argument("--output",required=True)
    ap.add_argument("--manifest",required=True)
    a=ap.parse_args()

    prefix=Path(a.prefix)
    if prefix.stat().st_size != a.prefix_size:
        raise SystemExit("prefix size mismatch")
    actual=sha256_file(prefix)
    if actual.lower() != a.prefix_sha256.lower():
        raise SystemExit("prefix SHA256 mismatch")

    rows=inventory(a.chunks)
    if not rows:
        raise SystemExit("no chunks found")
    pos=a.prefix_size
    manifest=[]
    for start,end,idx,p in rows:
        if start != pos:
            raise SystemExit(f"gap/overlap at chunk {idx}: start={start} expected={pos}")
        digest=sha256_file(p)
        manifest.append({"index":idx,"start":start,"end":end,"size":p.stat().st_size,"sha256":digest,"path":str(p)})
        pos=end+1
    if pos != a.remote_total:
        raise SystemExit(f"tail does not reach remote total: {pos} != {a.remote_total}")

    out=Path(a.output)
    if out.exists(): raise SystemExit("refusing to overwrite output")
    with open(out,"xb") as dst:
        with open(prefix,"rb") as src:
            while True:
                b=src.read(16*1024*1024)
                if not b: break
                dst.write(b)
        for row in manifest:
            with open(row["path"],"rb") as src:
                while True:
                    b=src.read(16*1024*1024)
                    if not b: break
                    dst.write(b)
        dst.flush()
        os.fsync(dst.fileno())

    if out.stat().st_size != a.remote_total:
        raise SystemExit("assembled size mismatch")
    full_sha=sha256_file(out)
    zip_ok=False
    bad=None
    members=0
    shards=0
    try:
        with zipfile.ZipFile(out) as z:
            bad=z.testzip()
            members=len(z.namelist())
            shards=sum(1 for n in z.namelist() if re.search(r"(^|/)conversations-\d+\.json$",n))
            zip_ok=bad is None
    except Exception as e:
        bad=type(e).__name__

    result={
      "format":"chatgpt_export_recovery_manifest_v1",
      "prefix":{"path":str(prefix),"size":a.prefix_size,"sha256":actual},
      "remote_total":a.remote_total,
      "chunks":manifest,
      "output":{"path":str(out),"size":out.stat().st_size,"sha256":full_sha},
      "zip_integrity":"PASS" if zip_ok else "FAIL",
      "zip_bad_member_or_error":bad,
      "zip_member_count":members,
      "conversation_shards":shards
    }
    Path(a.manifest).write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k!="chunks"},indent=2,ensure_ascii=False))
    return 0 if zip_ok else 4

if __name__=="__main__":
    raise SystemExit(main())
