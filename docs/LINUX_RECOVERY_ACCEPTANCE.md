# Linux live-recovery acceptance — 2026-11-04

An owner-authorized real export was recovered locally using the Python recovery
engine and an existing Linux Firefox session. No account identifiers, archive
contents, file links, cookies, private paths, or archive checksums are included
in this public record; the detailed result remains on the owner's computer.

Verified behavior:

- A direct file link was handed off from Chrome through the one-time loopback
  form on the same computer, without URL command arguments or request logs.
- The selected Firefox first-party session was read using a read-only SQLite
  transaction into memory, without cookie snapshots or cookie files.
- The adopted prefix of a stopped regular Chrome partial was compared with
  fresh validated Range data. The original file was preserved.
- A deliberate pause preserved the committed checkpoint. Resume rehashed it
  and checked the remote size and strong ETag before appending more data.
- Recovery continued with 64 MiB segments. An interrupted response was retried
  from the same committed offset without losing earlier verified bytes.
- The final file matched the frozen remote size. A full-file SHA256 matched the
  incremental digest, and every ZIP member passed CRC verification without
  extraction. The verified ZIP was published without overwriting another file.
- The output directory was mode 0700 and the final file mode 0600, owned by the
  current user. The final file and verified checkpoint shared the same inode.
- No archive, signed link, or cookie data was uploaded to another storage host.

The same code also passed 132 synthetic Python tests and compilation on native
Linux/Python 3.14. CLI flag/session lifecycle behavior is covered synthetically;
the live run used the package's engine and transport through a local console
launcher. The preserved Range engine and Linux/Firefox shell baseline were not
modified.

This acceptance is limited to that Linux recovery path. It does not establish
native Tk GUI behavior, Windows/macOS integration, packaged installers,
authenticated Chrome profile extraction, or support for Firefox containers.
Those require their own acceptance. No CI workflow or security bypass was added.
