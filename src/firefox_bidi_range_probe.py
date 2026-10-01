#!/usr/bin/env python3
import argparse, os, sqlite3, tempfile, time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service

def snapshot_cookie_db(src: Path, dst: Path):
    import shutil
    wal=Path(str(src)+"-wal"); shm=Path(str(src)+"-shm")
    if wal.is_file(): shutil.copy2(wal,Path(str(dst)+"-wal"))
    shutil.copy2(src,dst)
    if wal.is_file(): shutil.copy2(wal,Path(str(dst)+"-wal"))
    if shm.is_file(): shutil.copy2(shm,Path(str(dst)+"-shm"))

def load_rows(db: Path):
    c=sqlite3.connect(db)
    try:
        return c.execute("""SELECT name,value,host,path,expiry,isSecure,isHttpOnly,sameSite
        FROM moz_cookies WHERE lower(host)='chatgpt.com' OR lower(host) LIKE '%.chatgpt.com'""").fetchall()
    finally: c.close()

def add_cookie(d,row):
    name,value,host,path,expiry,secure,httponly,samesite=row
    cookie={"name":name,"value":value,"path":path or "/","secure":bool(secure),"httpOnly":bool(httponly)}
    if host.startswith("."): cookie["domain"]=host
    if expiry: cookie["expiry"]=int(expiry)
    if samesite==1: cookie["sameSite"]="Lax"
    elif samesite==2: cookie["sameSite"]="Strict"
    try: d.add_cookie(cookie); return True
    except Exception: return False

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--url-file",required=True)
    ap.add_argument("--profile",required=True)
    ap.add_argument("--start",type=int,required=True)
    ap.add_argument("--bytes",type=int,default=1048576)
    a=ap.parse_args()
    url=Path(a.url_file).read_text().splitlines()[0].strip()
    end=a.start+a.bytes-1
    with tempfile.TemporaryDirectory(prefix="ff-bidi-probe.") as td:
        td=Path(td); dl=td/"downloads"; dl.mkdir()
        snap=td/"cookies.sqlite"; snapshot_cookie_db(Path(a.profile)/"cookies.sqlite",snap)
        opts=Options(); opts.add_argument("-headless")
        opts.binary_location="/snap/firefox/current/usr/lib/firefox/firefox"
        opts.set_preference("browser.download.folderList",2)
        opts.set_preference("browser.download.dir",str(dl))
        opts.set_preference("browser.download.useDownloadDir",True)
        opts.set_preference("browser.download.alwaysOpenPanel",False)
        opts.set_preference("browser.helperApps.neverAsk.saveToDisk","application/zip,application/octet-stream,application/x-zip-compressed")
        svc=Service("/snap/bin/geckodriver",log_output=str(td/"geckodriver.log"))
        d=webdriver.Firefox(service=svc,options=opts)
        try:
            d.set_page_load_timeout(30)
            d.get("https://chatgpt.com/")
            added=sum(add_cookie(d,r) for r in load_rows(snap))
            d.get("https://chatgpt.com/")
            seen={}
            def req_handler(req):
                h=dict(req.headers)
                h["range"]=f"bytes={a.start}-{end}"
                h["accept-encoding"]="identity"
                req.set_headers(h)
            def resp_handler(resp):
                seen["status"]=resp.status
                seen["content_range"]=resp.headers.get("content-range")
                seen["url"]=resp.url
            pat=["**/backend-api/estuary/content**"]
            hid=d.network.add_request_handler(pat,req_handler)
            rid=d.network.add_response_handler(pat,resp_handler)
            try:
                try: d.get(url)
                except Exception: pass
                deadline=time.time()+60
                result=None
                while time.time()<deadline:
                    files=[p for p in dl.iterdir() if p.is_file() and not p.name.endswith(".part")]
                    if files:
                        result=max(files,key=lambda p:p.stat().st_mtime)
                        if result.stat().st_size>0: break
                    time.sleep(.25)
                print("COOKIES_ADDED",added)
                print("HTTP_STATUS",seen.get("status"))
                print("CONTENT_RANGE",seen.get("content_range"))
                print("FILE_BYTES",result.stat().st_size if result else 0)
                ok=result is not None and result.stat().st_size==a.bytes and seen.get("status")==206
                raise SystemExit(0 if ok else 4)
            finally:
                try:d.network.remove_request_handler(hid)
                except Exception:pass
                try:d.network.remove_response_handler(rid)
                except Exception:pass
        finally:d.quit()

if __name__=="__main__": main()
