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
