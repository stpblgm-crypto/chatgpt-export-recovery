# Limited Chromium live probe: 2026-10-01

**EXPERIMENTAL / NOT END-TO-END VERIFIED**

**Result: HOLD: DISK_CAPACITY.** An authenticated first segment was validated;
full recovery was not completed. This record contains only non-secret numeric
evidence and verification boundaries, not browser account information, archive
filenames, cookie values, signed URLs or the checkpoint's hash.

## Code and scope

- Repository revision: `2c97cf98b9798d376a91cb93ed9400571d14a5d3`
- Branch: `feature/dot-chromium-recovery-v1`
- Linux Chromium, explicit unpartitioned-only selection, pinned optional
  `browser-cookie3` adapter
- Existing incomplete archive used as an explicit in-place checkpoint
- Offline verification before the probe: 7 shell suites and 22 Python tests
  passed; no engine or test changes accompany this provenance update

The OS keyring lookup displayed a new-wallet setup dialog. Creation was
declined; no wallet was created. A retry with that dialog declined allowed the
upstream adapter's normal fallback to complete. No custom decryption, debugging
connection, alternate host, or access-control workaround was used. Cookie
values were not printed.

## Authenticated first-segment evidence

| Check | Observed result |
|---|---|
| Starting size guard | PASS: 25,127,845,888 bytes |
| Starting SHA256 guard | PASS; hash intentionally omitted |
| Requested range | `25127845888-25262063615` |
| Requested/body length | 134,217,728 bytes (128 MiB) |
| curl return code | 0 |
| HTTP status | 206 |
| Content-Range | `bytes 25127845888-25262063615/32841707548` |
| Start/end/body validation | PASS |
| Remote total disclosed | 32,841,707,548 bytes |

This proves that this session and export link supported this bounded range at
that time. It does not establish the rest of the archive's integrity, future
link validity, stability across later requests, or completed recovery.

## Disk gate and preservation

| Quantity at the gate | Bytes |
|---|---:|
| Remaining bytes from starting checkpoint | 7,713,861,660 |
| Required reserve | 1,073,741,824 |
| Required available space | 8,787,603,484 |
| Available space observed | 6,572,212,224 |
| Shortfall at that measurement | 2,215,391,260 |

The space gate returned **exit 28 before any append**. The validated temporary
segment was discarded. The original checkpoint remained at the same pathname
and size. A subsequent independent full-file SHA256 check matched the original
starting hash, confirming that its content was unchanged; the hash is omitted
from this record. Native-runtime cleanup verification found zero remaining temporary
session directories and confirmed that the private URL input file was absent.

No supported disk-resize mechanism was exposed in the available runtime tools.
No alternate host or storage workaround was used. Recovery is held until
adequate authorized capacity is available; link/session validity and available
space must be checked again before resuming.

## Not completed

- No recovery bytes appended during this probe
- No complete archive assembled
- No final ZIP integrity pass
- No final archive SHA256 established
- No claim of end-to-end Chromium verification

The preserved Firefox historical verification is separate from this limited
Chromium result. See [the experimental provider guide](CHROMIUM_EXPERIMENTAL.md)
for the permission boundary and recovery options.
