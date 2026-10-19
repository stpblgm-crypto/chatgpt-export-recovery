# Desktop candidate verification — 2026-10-19 UTC

Scope: Python/Tk desktop candidate on top of main `311cf238e8014a184cffe723d7d15901bb278ed4`.
No production deployment, browser profile reads, live exports, new credentials,
dependency installs, or hosted GitHub Actions runs were performed.

## Local results

Environment: Linux, Python 3.12.14; Tk 9.0 import available, no graphical display.

- `python -m unittest discover -s tests -p 'test_python_*.py' -v` — 87 tests passed
- `python -m compileall -q chatgpt_export_recovery launch.pyw` — passed
- `bash tests/run.sh` — all four legacy offline scripts passed
- `python -m chatgpt_export_recovery --version` — `0.2.0`
- `python -m chatgpt_export_recovery --help` — passed
- `python -m chatgpt_export_recovery` — correctly reports unavailable display;
  actual GUI rendering/interactions were not tested
- `git diff --check` — passed

Coverage uses deterministic in-memory ZIP payloads, fake Range responses, and
mocked urllib openers; no network calls or authenticated session are involved.
The independent core test pass identified malformed JSON, malformed deflate
streams and non-ASCII HTTP digit validation gaps; these were fixed and rerun.

## Covered contracts

- Exact Range/length/total validation, 200 and 416 rejection, strong/changed ETag,
  invalid encodings, malformed headers, TLS errors and unsafe redirects
- 401/403 expiry/auth errors, bounded temporary-failure retries, same-offset retry,
  response closure, no raw server text/signed links in diagnostics
- Pause mid-response, during backoff and verification; resume after checkpoint;
  lock contention, low disk, changed files, malformed metadata and crash suffix
- Browser partial import matched/mismatched/oversized/missing, source preserved
- Full ZIP CRC, malformed deflate and SHA256 failure, atomic no-overwrite publish
- Background worker events, duplicate-start rejection, safe unexpected errors,
  non-interactive CLI input rejection

## Not run / acceptance limits

Native Windows and macOS tests, Linux graphical UI acceptance, real HTTPS Range
integration, real ChatGPT export recovery, multi-gigabyte soak, packaged native
binaries/installers, and screen-reader acceptance have not been run. Tk imports
and controller tests do not establish GUI rendering or cross-platform success.

Hosted CI remains paused by the owner. No workflow was added or triggered. The
separate experimental Chromium draft PR remains untouched and its live-export
hold remains in place. The final acceptance decision belongs to the owner.
