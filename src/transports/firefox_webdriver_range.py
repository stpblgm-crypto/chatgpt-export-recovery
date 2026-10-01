#!/usr/bin/env python3
import argparse
import http.cookiejar
import json
import os
import pathlib
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

RANGE_RE = re.compile(r"^(0|[1-9][0-9]{0,17})-(0|[1-9][0-9]{0,17})$")
CONTENT_RANGE_RE = re.compile(r"^bytes [0-9]+-[0-9]+/[0-9]+$")

def fail(code, name):
    print("TRANSPORT_ERROR=" + name, file=sys.stderr)
    raise SystemExit(code)

def read_url_config(path):
    text = pathlib.Path(path).read_text(encoding="utf-8").strip()
    prefix = 'url = "'
    if not text.startswith(prefix) or not text.endswith('"'):
        raise ValueError("URL_CONFIG_INVALID")
    return text[len(prefix):-1]

def free_port():
    s=socket.socket(); s.bind(("127.0.0.1",0)); port=s.getsockname()[1]; s.close(); return port
def wd_request(port, method, path, payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data, method=method,
        headers={"Content-Type":"application/json;charset=UTF-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        error.read()
        raise RuntimeError("WEBDRIVER_HTTP_ERROR") from None

def wait_port(port, proc):
    deadline=time.time()+20
    while time.time()<deadline:
        if proc.poll() is not None:
            raise RuntimeError("GECKODRIVER_EXITED")
        try:
            with socket.create_connection(("127.0.0.1",port),timeout=.5):
                return
        except OSError:
            time.sleep(.2)
    raise RuntimeError("GECKODRIVER_START_TIMEOUT")

def add_cookies(port, sid, cookie_jar):
    jar=http.cookiejar.MozillaCookieJar(cookie_jar)
    jar.load(ignore_discard=True,ignore_expires=True)
    added=0
    for cookie in jar:
        domain=cookie.domain.lstrip(".")
        if not (domain=="chatgpt.com" or domain.endswith(".chatgpt.com")):
            continue
        payload={"name":cookie.name,"value":cookie.value,"path":cookie.path or "/",
                 "domain":cookie.domain,"secure":bool(cookie.secure)}
        if cookie.expires:
            payload["expiry"]=int(cookie.expires)
        try:
            wd_request(port,"POST",f"/session/{sid}/cookie",{"cookie":payload},10)
            added+=1
        except Exception:
            pass
    return added
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--url-config",required=True)
    parser.add_argument("--cookie-jar",required=True)
    parser.add_argument("--range",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--meta",required=True)
    parser.add_argument("--headless",action="store_true")
    args=parser.parse_args()

    match=RANGE_RE.fullmatch(args.range)
    if not match:
        raise SystemExit(2)
    start,end=map(int,match.groups())
    if end<start:
        raise SystemExit(2)
    expected=end-start+1
    url=read_url_config(args.url_config)
    output=pathlib.Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    download_name="webdriver-segment.bin"
    download_path=output.parent/download_name
    partial_path=pathlib.Path(str(download_path)+".part")
    for path in (download_path,partial_path):
        try:path.unlink()
        except FileNotFoundError:pass

    port=free_port()
    log_path=output.parent/"webdriver-transport.log"
    log=open(log_path,"w")
    proc=subprocess.Popen(["geckodriver","--port",str(port),"--log","error"],stdout=log,stderr=log)
    sid=None
    status=-1; content_range="INVALID"; size=0
    try:
        wait_port(port,proc)
        prefs={
            "browser.download.folderList":2,
            "browser.download.dir":str(output.parent),
            "browser.download.useDownloadDir":True,
            "browser.download.alwaysOpenPanel":False,
            "browser.helperApps.neverAsk.saveToDisk":"application/octet-stream,application/zip",
        }
        ffargs=["-headless"] if args.headless else []
        caps={"capabilities":{"alwaysMatch":{"browserName":"firefox",
              "pageLoadStrategy":"eager",
              "moz:firefoxOptions":{"args":ffargs,"prefs":prefs}}}}
        created=wd_request(port,"POST","/session",caps,60)
        sid=created["value"]["sessionId"]
        wd_request(port,"POST",f"/session/{sid}/timeouts",{"script":120000,"pageLoad":30000,"implicit":0})
        wd_request(port,"POST",f"/session/{sid}/url",{"url":"https://chatgpt.com/"},40)
        add_cookies(port,sid,args.cookie_jar)
        wd_request(port,"POST",f"/session/{sid}/url",{"url":"https://chatgpt.com/"},40)
        script="""
const done=arguments[arguments.length-1];
const url=arguments[0], start=arguments[1], end=arguments[2], name=arguments[3];
const ctl=new AbortController();
const timer=setTimeout(()=>ctl.abort(),90000);
fetch(url,{credentials:'include',headers:{'Range':'bytes='+start+'-'+end},signal:ctl.signal})
.then(async r=>{
 const cr=r.headers.get('content-range');
 if(r.status!==206){clearTimeout(timer);try{await r.body.cancel();}catch(e){};done({status:r.status,contentRange:cr,size:0});return;}
 const blob=await r.blob();
 const a=document.createElement('a');
 const objectUrl=URL.createObjectURL(blob);
 a.href=objectUrl;a.download=name;document.body.appendChild(a);a.click();a.remove();
 setTimeout(()=>URL.revokeObjectURL(objectUrl),5000);
 clearTimeout(timer);done({status:r.status,contentRange:cr,size:blob.size});
}).catch(e=>{clearTimeout(timer);done({status:-1,contentRange:null,size:0});});
"""
        result=wd_request(port,"POST",f"/session/{sid}/execute/async",
                          {"script":script,"args":[url,start,end,download_name]},130)
        value=result.get("value",{})
        status=int(value.get("status",-1))
        content_range=value.get("contentRange") or "INVALID"
        size=int(value.get("size",0))
        if status==206:
            deadline=time.time()+120
            last=-1; stable=0
            while time.time()<deadline:
                if download_path.exists() and not partial_path.exists():
                    current=download_path.stat().st_size
                    if current==last and current==expected:
                        stable+=1
                        if stable>=2:break
                    else:stable=0
                    last=current
                time.sleep(.5)
            if not download_path.exists() or download_path.stat().st_size!=expected:
                raise RuntimeError("DOWNLOAD_SIZE_MISMATCH")
            os.replace(download_path,output)
    finally:
        if sid:
            try:wd_request(port,"DELETE",f"/session/{sid}",None,10)
            except Exception:pass
        proc.terminate()
        try:proc.wait(timeout=5)
        except Exception:proc.kill()
        log.close()

    safe_range=content_range if CONTENT_RANGE_RE.fullmatch(content_range) else "INVALID"
    pathlib.Path(args.meta).write_text(
        f"http={status}\ncontent_range={safe_range}\nbody_bytes={size}\n",
        encoding="ascii")
    if status in (401,403) or 400<=status<=499:return 22
    if status!=206:return 1
    return 0

if __name__=="__main__":
    import sys
    raise SystemExit(main())
