# Browser-backed Range Recovery Bridge

Status: EXPERIMENTAL / local canary required before tail recovery.

This bridge exists for protected export endpoints that accept the browser's own
network stack but reject standalone HTTP clients with HTTP 403.

## Design

The Opera/Chromium extension:
- discovers the newest ChatGPT export source URL from the browser's own download history;
- never writes or prints the signed URL;
- issues explicit Range requests through chrome.downloads.download;
- first downloads a 1 MiB canary from a byte range already present in the local prefix;
- requires local byte-for-byte/hash verification of that canary before tail recovery;
- downloads the remaining tail as fixed 64 MiB chunks;
- records progress in chrome.storage.local and resumes from the next chunk after an interruption.

The local assembler:
- verifies the known prefix size and SHA-256;
- validates contiguous chunk geometry with no gaps/overlaps;
- hashes every chunk;
- concatenates prefix + chunks into a new output;
- verifies final size, SHA-256, ZIP integrity, member count, and conversation shard count.

## Security

- The repository contains no signed export URL.
- The runtime extension obtains the URL only from the browser's own download record.
- Do not commit browser profiles, cookies, signed URLs, or recovered export content.
- Do not treat a browser download marked complete as valid until ZIP integrity passes.

## Current recovery geometry (2026-10-01)

Known remote total: 32841707548 bytes.
Current browser-produced prefix: 19426525184 bytes.
Remaining tail: 13415182364 bytes.
Nominal tail chunk size: 67108864 bytes (64 MiB).

A canary must be compared against the same known-prefix range before full tail download starts.
