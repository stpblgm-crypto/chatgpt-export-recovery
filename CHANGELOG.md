# Changelog

## Unreleased — 2026-11-04

- Add explicit local clipboard and one-time loopback browser form inputs to the
  Python CLI, keeping the hidden prompt as the default.
- Keep direct links out of command arguments and logs; guard the form with a
  random path, strict Host/Origin checks, bounded input, and a short lifetime.
- Add synthetic tests for privacy, request rejection, lifecycle, and CLI input
  selection. The Range/checkpoint engine and Firefox shell baseline are unchanged.
- These modes accept direct file links; authenticated Chrome session recovery
  remains unimplemented. Native full-download acceptance remains pending.

## 0.2.0 candidate — 2026-10-19

- Add a standard-library Python/Tk desktop app targeting Windows/Linux/macOS.
- Add browser-independent direct-link input without cookie/profile access.
- Add bounded Range retry, cancellable workers, crash-resumable hash-bound
  checkpoints, strong ETag validation, original-preserving partial import,
  ZIP/SHA256 verification, and atomic no-overwrite publication.
- Add synthetic Python regression tests and desktop acceptance documentation.
- Preserve the Linux/Firefox baseline and separate experimental CDP draft.
- Native platform/live-export acceptance and packaged installers remain pending.


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

