# Browser support roadmap

## Current provider

Firefox on Linux is implemented. The verified run used Firefox Snap. Regular
Firefox and Firefox Flatpak paths are discovered, but Flatpak has not received
an end-to-end real export run.

Firefox-specific state includes `profiles.ini`, `cookies.sqlite`, its WAL/SHM
sidecars, and container partitioning through `originAttributes`.

## Experimental Chromium provider

Chromium and Chrome now have an EXPERIMENTAL / NOT END-TO-END VERIFIED provider.
Bounded profile discovery, explicit profile selection, a default plaintext-only
backend, and an opt-in pinned `browser-cookie3` adapter are implemented. Synthetic
tests cover version-24 v10 cookies without accessing a real OS keyring. Unknown
encryption fails closed. Partitioned cookies fail closed by default; explicit
unpartitioned-only selection omits them without merging values.
A [bounded authenticated live probe](LIVE_PROBE_2026-10-01.md) passed HTTP 206
validation, then stopped for insufficient disk before append. No complete live
recovery is claimed; the current hold is DISK_CAPACITY.
See [the experimental guide](CHROMIUM_EXPERIMENTAL.md) for runtime boundaries.

## Further verification and providers

Planned investigation order:

1. Google Chrome and Chromium on Linux.
2. Chromium Snap.
3. Brave and Microsoft Edge.
4. Optional Opera and Vivaldi compatibility if their profile/keyring behavior
   can reuse a proven Chromium provider.

Chromium-family cookies may use `encrypted_value` and an operating-system
keyring such as Secret Service/libsecret. A provider must use documented local
OS facilities, select `Default` or `Profile N` explicitly, account for
Snap/Flatpak paths, keep decrypted values ephemeral, and fail closed when safe
decryption is unavailable.

## Verification bar

A new browser moves from `NOT IMPLEMENTED` to `VERIFIED` only after synthetic
tests, secret-output tests, interruption/resume tests, and a complete real-world
ZIP recovery with recorded provenance. Merely finding a profile path is not
end-to-end verification.
