# Chromium: EXPERIMENTAL / NOT LIVE VERIFIED

This provider has passed synthetic tests only. Browser/profile detection is not
proof of usable authentication, and a complete live ZIP recovery has not been
verified. The Firefox provider and historical verified-run record are preserved.

## Runtime and permission boundary

Use this utility only where the current runtime's documented permissions allow
reading your selected browser session and sending that session to the intended
ChatGPT/OpenAI export service. A browser visible in another environment does not
make its filesystem, cookies, debugging connection, or credentials available to
this process. Do not attach to a private debugging pipe, inspect hidden runtime
state, disable browser encryption, or work around a denied operation.

Status remains EXPERIMENTAL / NOT LIVE VERIFIED with either partition option.

The provider does not use CDP, inspect process descriptors, launch browsers,
automate login/MFA, or open a remote debugging port. If permitted session access
is unavailable, stop. There is no fallback to another user's or another
runtime's profile.

## Profile selection

`--browser chromium` selects the experimental provider explicitly. `--browser
auto` honors an explicit provider override, otherwise prefers the existing
Firefox detector, then Chromium profile detection. Both provider overrides at
once require an explicit browser choice. A Firefox detection followed by an
authentication failure does not silently switch browser/account.

`--profile DIR` means one profile directory, such as `Default` or `Profile 1`,
not the parent user-data directory. Modern `Network/Cookies` takes precedence
over legacy `Cookies`. Without an explicit profile, bounded discovery checks
Chromium and Google Chrome XDG config, Chromium Snap, and their listed Flatpak
layouts. Only `Default` and `Profile N` are considered. Multiple candidates
fail closed. The `CHATGPT_RECOVERY_CHROMIUM_COOKIE_DB` environment override
selects one database exclusively and conflicts with `--profile`.

## Cookie backends

The default `--cookie-backend plaintext` uses Python's standard `sqlite3` in a
read-only transaction. It supports unencrypted synthetic/legacy cookies only.
An active encrypted matching cookie fails the whole operation before any jar
is written. This mode never contacts the OS keyring.

For an explicitly authorized own-session workflow, the optional adapter uses
[upstream browser-cookie3 0.20.1](https://pypi.org/project/browser-cookie3/0.20.1/):

```bash
python3 -m venv /a/private/temporary/recovery-venv
/a/private/temporary/recovery-venv/bin/pip install -r requirements-chromium.txt
PATH="/a/private/temporary/recovery-venv/bin:$PATH" \
  ./src/chatgpt-export-recover \
  --browser chromium --profile /authorized/profile/Default \
  --cookie-backend browser-cookie3 \
  --output /chosen/location/export.zip
```

Installation is never automatic. The installed adapter must be exactly 0.20.1;
this pin is tested compatibility, not a claim that future versions are unsafe.
The Python selected through PATH must be the authorized environment containing
that dependency. The optional adapter may access the operating-system keyring
using upstream facilities; approve that access for the current runtime before
selecting it. For Chrome use `--chromium-product chrome`; Chromium is the default.

Only valid ChatGPT/OpenAI-domain rows enter a temporary filtered SQLite
snapshot. Exact domain boundaries are checked, session expirations are
normalized, and `meta.version` is preserved, including version 24's domain hash
prefix. Partitioned cookies are rejected by default. The explicit
`--cookie-partition unpartitioned` option narrows selection to unpartitioned
cookies only, before inspecting encrypted values or calling the dependency.
Every row with a nonempty `top_frame_site_key` is omitted, including a
first-party partition; no partitioned cookie is flattened or merged into the
jar. If no usable unpartitioned cookies remain, the operation fails. This
option is appropriate only when the authorized request intends the top-level,
unpartitioned session; it does not prove that this session can authenticate the
export. The default `--cookie-partition reject` retains mixed-partition failure.
Unknown encryption prefixes (including `v20`) in selected cookies are rejected
before calling the dependency. Linux `v10`/`v11` handling is
upstream code; this repository implements no decryption algorithm. Unknown
schemas, unavailable keys, timeout, unexpected returned cookies or an incomplete
jar fail closed. The dependency has a 20-second invocation limit. Its temporary
snapshot is removed after use, and exit cleanup removes the private jar.

The default user-agent is an honest experimental utility identifier. A browser
user-agent obtained through a permitted runtime interface may be supplied in
`CHATGPT_RECOVERY_CHROMIUM_USER_AGENT`. No browser is probed to obtain it, and no
anti-bot compatibility is promised.

## Preserve an existing incomplete ZIP filename

The default behavior still adopts an invalid final file as `.part`. For an
approved existing file that must stay at its current pathname, use the same
path for output and the explicit checkpoint:

```bash
./src/chatgpt-export-recover \
  --browser chromium --profile /authorized/profile/Default \
  --cookie-backend browser-cookie3 \
  --output "$EXISTING_ARCHIVE" --checkpoint-file "$EXISTING_ARCHIVE" \
  --expected-size "$APPROVED_START_SIZE" \
  --expected-sha256 "$APPROVED_START_SHA256"
```

Supply the independently established size and SHA256 for the intended starting
file. Both are checked before any export request or checkpoint mutation. No
25 GB copy or initial rename is needed. The first range starts at that exact
size. The first bounded segment must disclose a consistent total, and free
space at the checkpoint must cover all remaining bytes plus 1 GiB before append.
A 401/403, 200, malformed or incorrect range, failed guard, or insufficient free
space does not append any data. Successfully validated later segments may
advance the checkpoint before a subsequent failure; update the expected guards
from that intentional checkpoint before the next explicit in-place invocation.
A complete but ZIP-invalid file stays in place and is never called successful.

HTTP total is frozen within one invocation. It is not persisted between runs;
the caller must use a link to the same export. A file size/hash establishes the
local starting file's identity, not the remote archive's identity. Keep the
browser and other downloaders from writing the selected file concurrently.

## Transport limits and tests

curl 8.4.0 or newer is required because older versions cannot enforce
`--max-filesize` when Content-Length is missing. Each request caps the response
body at the requested segment length, permits at most five redirects, and has
a five-minute time limit. See the [curl manual](https://curl.se/docs/manpage.html#--max-filesize).
Raw response headers and curl errors are never printed. Only numeric, validated
range fields enter diagnostics. Retries use 128/64/32/16 MiB by default.

Run `./tests/run.sh` for seven shell suites and the synthetic Python suite. The
standard-library run skips one optional-library test when the dependency is
absent. Run the Python suite with the isolated environment to cover the real
pinned library against a generated version-24 `v10` fixture. That test mocks OS
keyring access and reads no live profile. No tests download a real export.

Partition detection follows Chromium's serialized `top_frame_site_key`; the
ancestor-chain bit alone does not imply a partition. See the
[Chromium cookie store source](https://chromium.googlesource.com/chromium/src/+/refs/tags/131.0.6761.0/net/extras/sqlite/sqlite_persistent_cookie_store.cc).
The Python ZIP fallback checks `ZipFile.testzip()` explicitly and treats every
bad-member result or exception as failure, rather than relying on CLI output.
