# ChatGPT Export Recovery

Resumable, verified recovery downloader for large ChatGPT data export ZIP
archives on Linux.

> Unofficial utility. Not affiliated with or endorsed by OpenAI.

Large browser downloads can stop before the complete archive reaches disk. This
tool reuses an authenticated local Firefox session, downloads bounded HTTP Range
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
- `curl`, Python 3 with `sqlite3`, `stat`, `sync`, and `sha256sum`
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
| Chrome | NOT IMPLEMENTED |
| Chromium | NOT IMPLEMENTED |
| Brave | NOT IMPLEMENTED |
| Edge | NOT IMPLEMENTED |

`VERIFIED` refers to the preserved 2026-08-26 real-world Firefox baseline and,
where applicable, the included offline regression tests. The modular refactor
has not yet received a second live export run.

## Tests

No ChatGPT account or network access is required:

```bash
./tests/run.sh
```

The suite checks every shell file with `bash -n`, exercises accepted and
rejected Range responses, verifies a resumed append, and uses a synthetic
Firefox cookie database to prove that diagnostics do not expose cookie values.

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
- `src/browsers/firefox.sh` — Firefox session provider
- `src/lib/download.sh` — browser-independent Range/checkpoint engine
- `legacy/` — sanitized archival baseline; not the primary interface
- `docs/` — architecture, security, verified-run provenance, and roadmap
- `tests/` — offline regression suite

## Limitations and roadmap

This release is Linux-only and has no GUI, extension, login automation,
telemetry, scheduled export, or external SaaS dependency. Chromium-family
support requires an OS-keyring-aware provider; browser encryption will not be
bypassed. See [the browser roadmap](docs/BROWSER_SUPPORT_ROADMAP.md).

## License

[MIT](LICENSE). The repository consists of the supplied original baseline and
the original bootstrap/refactor code; no third-party source fragments are
included.
