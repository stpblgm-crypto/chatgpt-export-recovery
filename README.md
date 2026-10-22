# ChatGPT Export Recovery

A Python desktop app for resumable, verified ChatGPT export ZIP downloads,
with a preserved Linux/Firefox shell implementation and an experimental
Chromium CDP provider.

> Unofficial utility. Not affiliated with or endorsed by OpenAI.

## Python desktop app (0.2.0 candidate)

Windows, Linux, and macOS source targets, with a native Tk interface and no
third-party runtime dependencies. Start from the repository folder:

```text
python -m chatgpt_export_recovery
```

Use `py` on Windows or `python3` on macOS/Linux as appropriate. Python 3.10+
with Tk is required. Paste a direct HTTPS export download link from **any
browser**, select a new ZIP output, and Start / resume. Optional browser
partial import preserves the original. Pause, bounded retries, checkpoint
hash verification, remote ETag checks, ZIP CRC, SHA256, and no-overwrite
publication are built in. The UI stays responsive while a worker downloads.

The server must support standalone HTTPS Range requests with a strong ETag.
The app does not read browser cookies, decrypt profiles, or automate login.
A session-only export endpoint is not supported by the universal link mode.
Browser partial import re-fetches the prefix to verify it; normal app resume
uses its verified checkpoint without re-downloading committed bytes.

[Launch, browser compatibility, privacy, storage, and acceptance guide](docs/DESKTOP_APP.md)

- Python download/controller tests: synthetic local coverage
- Native Windows/macOS GUI acceptance: pending
- Linux graphical acceptance: pending (headless development environment)
- Real export in the new desktop app: not yet tested
- Native installers and signed binaries: not included

## Preserved Linux / Firefox CLI

The existing shell tool reuses an authenticated local Firefox session,
downloads bounded HTTP Range segments, validates every response before append,
and resumes from the exact size of its durable `.part` checkpoint. Its verified
real-world baseline is Linux with Firefox Snap and 128 MiB segments.
The sections below describe that original shell implementation, not the new
Python GUI. Experimental Chromium CDP is described separately below; its
browser-authenticated transport is not used by the Python direct-link app.

## Safety first

A signed export URL is a short-lived, credential-like locator. Cookie values,
the URL, browser databases, and export archives must never be committed or
shared. This repository contains no live signed URL: the archival baseline has
one explicit placeholder in its place.

The safest invocation uses the hidden prompt, keeping the URL out of the typed
command:

```bash
./src/chatgpt-export-recover \
  --output "$HOME/Downloads/chatgpt-data-export.zip" \
  --browser auto
```

The prompt does not echo the URL. `CHATGPT_EXPORT_URL`, `--url-file`, and
`--url '<export-url>'` are also supported; `--url` can expose the value through
shell history or the CLI process listing. During transfer, curl reads the URL
from a mode-0600 temporary config so it is not placed in curl's argument list.

## Prerequisites

- Linux and Bash 4 or newer
- `curl`, Python 3 with `sqlite3`, `stat`, `sync`, and `sha256sum`
- `unzip`, or Python's standard `zipfile` module as fallback
- for Firefox mode: a local Firefox profile signed in to the same ChatGPT account
- for Chromium CDP mode: an already-running Chromium-family ChatGPT/OpenAI page
  with a local DevTools endpoint exposed by that browser/runtime
- enough free space for the remaining bytes plus a 1 GiB safety margin

Firefox profile discovery checks a running process first, then bounded known
layouts for regular Firefox, Firefox Snap, and Firefox Flatpak. It does not
recursively crawl the browser tree.

## How it works

For each segment the download engine requires all of the following before it
changes the durable checkpoint:

1. HTTP status is exactly `206`.
2. `Content-Range` is present and starts at `stat(PART).size`.
3. The response does not exceed the requested range.
4. The body length exactly matches the declared range length.
5. The remote total remains unchanged for the whole run.

Only then does the engine append, verify the new file size, synchronize the
filesystem, and advance the checkpoint. Transport failures retry the same
offset with progressively smaller segments down to 16 MiB; body-length failures
receive the same bounded adaptive treatment. An interrupted or invalid current
segment is discarded; already verified `.part` bytes remain.

At completion, the assembled size must equal the frozen remote total and the
ZIP integrity test must pass. The tool then renames `.part` to the requested
final path and prints its SHA256.

## Resume

Run the same command again with the same `--output`. If
`chatgpt-data-export.zip.part` exists, its exact size becomes the next Range
start. A valid final ZIP exits immediately. An invalid final file becomes the
checkpoint only when no checkpoint already exists; otherwise it is preserved
under a no-overwrite timestamped name.

## Legacy shell support matrix

| Capability | State |
|---|---|
| Firefox Linux | VERIFIED |
| Firefox Snap | VERIFIED |
| Resume existing partial | VERIFIED |
| HTTP Range validation | VERIFIED |
| 128 MiB segmented recovery | VERIFIED |
| Final ZIP integrity test | VERIFIED |
| Firefox Flatpak | NOT YET VERIFIED |
| Chrome via local CDP | EXPERIMENTAL |
| Chromium via local CDP | EXPERIMENTAL |
| Brave | NOT IMPLEMENTED |
| Edge | NOT IMPLEMENTED |

`VERIFIED` refers to the preserved 2026-08-26 real-world Firefox baseline and,
where applicable, the included offline regression tests. The modular refactor
has not yet received a second live export run.

## Tests

Python desktop engine/controller (all platforms, standard library only):

```text
python -m unittest discover -s tests -p "test_python_*.py" -v
```

Linux shell/CDP suite. No ChatGPT account or external network is required;
the synthetic transport fixture uses its own loopback socket:

```bash
./tests/run.sh
```

The suite checks every shell file with `bash -n`, exercises accepted and
rejected Range responses, verifies a resumed append, uses a synthetic Firefox
cookie database to prove that diagnostics do not expose cookie values, and runs
a synthetic local CDP server to verify a browser-native 206 Range fetch without
leaking the signed URL.

## Troubleshooting

- **No Firefox database found:** start Firefox with the intended profile and
  keep it open, then retry.
- **No valid ChatGPT/OpenAI cookies:** sign in using that Firefox profile. The
  tool does not automate login, MFA, or anti-bot challenges.
- **HTTP 401/403:** refresh the legitimate export link or authenticated session;
  the checkpoint remains unchanged.
- **HTTP 200 instead of 206:** the response is rejected because it cannot be
  safely appended.
- **Low disk space:** free space and rerun; verified checkpoint bytes remain.
- **ZIP test fails after exact assembly:** the `.part` file is retained for
  investigation and no automatic full redownload begins.

## Repository layout

- `chatgpt_export_recovery/` — Python desktop app, engine, controller and CLI
- `launch.pyw` — desktop launch helper
- `src/chatgpt-export-recover` — legacy Linux CLI
- `src/browsers/firefox.sh` — Firefox session provider
- `src/browsers/chromium_cdp_fetch.py` — experimental browser-native Chromium CDP segment transport
- `src/lib/download.sh` — Firefox/curl Range/checkpoint engine
- `src/lib/download_cdp.sh` — Chromium CDP Range/checkpoint engine
- `legacy/` — sanitized archival baseline; not the primary interface
- `docs/` — architecture, security, verified-run provenance, and roadmap
- `tests/` — offline regression suite

## Limitations and roadmap

The preserved shell implementation is Linux-only. The Python desktop candidate
targets Windows, Linux, and macOS using standalone direct links. Neither route
has an extension, login automation, telemetry, scheduled export, or external
SaaS dependency. Experimental Chromium CDP is browser-native and does not
decrypt its cookie store; it requires an existing local DevTools endpoint and
fails closed when unavailable. It is separate from the Python direct-link
route. Browser encryption is not bypassed. See
[the browser roadmap](docs/BROWSER_SUPPORT_ROADMAP.md).

## License

[MIT](LICENSE). The repository consists of the supplied original baseline and
the original bootstrap/refactor code; no third-party source fragments are
included.


## Experimental Chromium CDP recovery

For a Chromium-family browser whose existing authenticated ChatGPT/OpenAI page
is reachable through a local DevTools endpoint:

```bash
./src/chatgpt-export-recover \
  --output "$HOME/Downloads/chatgpt-data-export.zip" \
  --browser chromium-cdp
```

If an invalid browser-downloaded final file already exists at the output path,
the tool first moves it to `.part`, then requests only bytes starting at that
exact checkpoint. The default CDP segment is 8 MiB with an adaptive floor of
1 MiB.

The provider uses `Network.loadNetworkResource(includeCredentials=true)` and
`IO.read` inside the existing browser. It does **not** read or decrypt the
Chromium cookie database and does not print the signed export URL.

If auto-discovery cannot find the browser's local DevTools HTTP endpoint, an
explicit local endpoint may be supplied only for that runtime:

```bash
CHATGPT_RECOVERY_CDP_ENDPOINT=http://127.0.0.1:9222 \
  ./src/chatgpt-export-recover --browser chromium-cdp --output /path/export.zip
```

This mode remains **EXPERIMENTAL** until a complete live export recovery passes
HTTP Range validation, full ZIP integrity, and provenance recording.
