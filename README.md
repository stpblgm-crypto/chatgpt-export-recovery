# ChatGPT Export Recovery

Resumable, verified recovery downloader for large ChatGPT data export ZIP
archives on Linux.

> Unofficial utility. Not affiliated with or endorsed by OpenAI.

Large browser downloads can stop before the complete archive reaches disk. This
tool reuses an authenticated local Firefox session (or an explicitly selected
experimental Chromium provider), downloads bounded HTTP Range
segments, validates every response before append, and resumes from the exact
size of the durable `.part` checkpoint.

The verified real-world baseline is Linux with Firefox Snap and 128 MiB
segments. The modular entrypoint is structured so browser providers can be
added without changing the download engine.

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
- `curl` 8.4.0 or newer, Python 3 with `sqlite3`, `stat`, `sync`, and `sha256sum`
- `unzip`, or Python's standard `zipfile` module as fallback
- a local Firefox profile signed in to the same ChatGPT account as the export
- enough free space for the remaining bytes plus a 1 GiB safety margin

Firefox profile discovery checks a running process first, then bounded known
layouts for regular Firefox, Firefox Snap, and Firefox Flatpak. It does not
recursively crawl the browser tree.

## How it works

For each segment the download engine requires all of the following before it
changes the durable checkpoint:

1. HTTP status is exactly `206`.
2. `Content-Range` is present and starts at `stat(PART).size`.
3. The response ends exactly at the requested end or the object's final byte.
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

For an existing incomplete ZIP that must retain its filename, the new
`--checkpoint-file FILE` option supports in-place recovery with mandatory
`--expected-size` and `--expected-sha256` guards. See the
[experimental Chromium guide](docs/CHROMIUM_EXPERIMENTAL.md) for the complete
permission boundary, options, and recovery procedure.

## Support matrix

| Capability | State |
|---|---|
| Firefox Linux | VERIFIED |
| Firefox Snap | VERIFIED |
| Resume existing partial | VERIFIED |
| HTTP Range validation | VERIFIED |
| 128 MiB segmented recovery | VERIFIED |
| Final ZIP integrity test | VERIFIED |
| Firefox Flatpak | NOT YET VERIFIED |
| Chrome | EXPERIMENTAL / NOT END-TO-END VERIFIED |
| Chromium | EXPERIMENTAL / NOT END-TO-END VERIFIED |
| Brave | NOT IMPLEMENTED |
| Edge | NOT IMPLEMENTED |

`VERIFIED` refers to the preserved 2026-08-26 real-world Firefox baseline and,
where applicable, the included offline regression tests. The modular refactor
has not yet received a second live export run.

## Limited live probe (2026-10-01)

The experimental Chromium provider completed one authenticated 128 MiB Range
probe with exact HTTP 206 boundaries and matching body length. The existing
checkpoint's starting size/SHA256 guards passed. The remote total showed that
available disk space was insufficient for the remaining bytes plus the 1 GiB
reserve, so the tool exited with code 28 **before any append**. The original
checkpoint stayed at its original pathname and size; an independent post-attempt
SHA256 check confirmed unchanged content. Temporary session material and the
private URL input were cleaned up.

Full recovery, final ZIP integrity and a final archive SHA256 were **not**
completed. Status: **HOLD: DISK_CAPACITY**. No supported resize mechanism was
available in that runtime. See the [limited live-probe record](docs/LIVE_PROBE_2026-10-01.md)
for numeric evidence and verification boundaries.

## Tests

No ChatGPT account or network access is required:

```bash
./tests/run.sh
```

The suite checks every shell file with `bash -n`, exercises accepted and
rejected Range responses, verifies a resumed append, and uses a synthetic
Firefox cookie database to prove that diagnostics do not expose cookie values.
It also covers explicit/automatic Chromium selection, synthetic cookie schemas,
secret-safe failure, optional pinned-library v10/version-24 handling, exact
in-place resume, wrong size/hash guards, invalid final ZIP retention, bounded
adaptive retries, and stable-total enforcement. No live browser is accessed.

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

- `src/chatgpt-export-recover` — current CLI
- `src/browsers/firefox.sh` — preserved Firefox session provider
- `src/browsers/chromium.sh` and `chromium_cookies.py` — experimental provider
- `src/lib/download.sh` — browser-independent Range/checkpoint engine
- `legacy/` — sanitized archival baseline; not the primary interface
- `docs/` — architecture, security, verified-run provenance, and roadmap
- `tests/` — offline regression suite

## Limitations and roadmap

This release is Linux-only and has no GUI, extension, login automation,
telemetry, scheduled export, or external SaaS dependency. Chromium support is EXPERIMENTAL / NOT END-TO-END VERIFIED. The default plaintext
backend fails on encrypted cookies; an explicitly selected, pinned
`browser-cookie3` adapter supports authorized local OS facilities without
custom cryptography or CDP. See [the guide](docs/CHROMIUM_EXPERIMENTAL.md) and
[the browser roadmap](docs/BROWSER_SUPPORT_ROADMAP.md).

## License

[MIT](LICENSE). The repository consists of the supplied original baseline and
the original bootstrap/refactor code; no third-party source fragments are
included.
