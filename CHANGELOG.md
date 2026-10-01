# Changelog

All notable changes to this project are documented here.

## 0.1.0 - 2026-08-26

- Preserve the verified Firefox V6 implementation as a sanitized archival
  baseline.
- Add a browser-independent, checkpointed HTTP Range download engine.
- Add a Firefox provider with bounded profile discovery, SQLite/WAL snapshots,
  origin-partition selection, and ephemeral cookie jars.
- Add a CLI with hidden URL prompting, environment/file URL inputs, resume, ZIP
  verification, and final SHA256 output.
- Add offline tests for shell syntax, Range validation, resume append behavior,
  cookie filtering, and secret-free diagnostics.

## Unreleased: experimental Chromium recovery

- Add explicit Chromium/Chrome session provider and deterministic auto selection.
- Add plaintext-only default and opt-in browser-cookie3 0.20.1 Linux adapter.
- Add expected-size/SHA256 guarded in-place checkpoint support.
- Tighten range-end and header validation; cap response bytes and request time.
- Extend offline synthetic/provider/resume/secret-output coverage.
- Preserve Firefox source and historical live verification; Chromium is
  EXPERIMENTAL / NOT LIVE VERIFIED.

### Experimental partition selection follow-up

- Add explicit `--cookie-partition unpartitioned` narrowing for an authorized
  top-level session. All partitioned rows are omitted before adapter access.
- Keep default mixed-partition rejection and experimental/unverified status.
- Add omission, unsupported-prefix isolation, empty-selection and invalid-option
  regression tests. No partitioned values are merged into the output jar.
