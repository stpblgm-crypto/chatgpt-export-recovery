# Browser support roadmap

## Current provider

Firefox on Linux is implemented. The verified run used Firefox Snap. Regular
Firefox and Firefox Flatpak paths are discovered, but Flatpak has not received
an end-to-end real export run.

Firefox-specific state includes `profiles.ini`, `cookies.sqlite`, its WAL/SHM
sidecars, and container partitioning through `originAttributes`.

## Chromium CDP experimental provider

A first Chromium-family provider now exists on
`feature/chromium-cdp-resume-v1`. It deliberately avoids Chromium cookie-store
decryption. Instead it requires an already-running authenticated ChatGPT/OpenAI
page with a local DevTools endpoint and asks the browser network stack to issue
the Range request with `includeCredentials=true`.

Security properties:
- no Chromium cookie database reads;
- no OS-keyring extraction;
- no cookie values printed or persisted;
- signed URL held only in mode-0600 runtime material;
- local-loopback CDP only;
- fail closed if no ChatGPT/OpenAI CDP target is reachable.

The provider is **EXPERIMENTAL**, not VERIFIED. A complete live export recovery
must still prove CDP availability in the target runtime, authenticated 206 Range
semantics, interruption/resume, final ZIP integrity, and no secret leakage.

## Next providers / hardening

Planned investigation order:

1. Complete a real Chromium CDP recovery and record provenance.
2. Harden Chromium/Chrome/Chromium-Snap endpoint discovery.
3. Brave and Microsoft Edge via the same CDP transport where compatible.
4. Optional Opera/Vivaldi compatibility.
5. Only if browser-native CDP is unavailable, investigate an OS-keyring-aware
   cookie provider. Browser encryption must not be bypassed.

## Verification bar

A new browser moves from `NOT IMPLEMENTED` to `VERIFIED` only after synthetic
tests, secret-output tests, interruption/resume tests, and a complete real-world
ZIP recovery with recorded provenance. Merely finding a profile path is not
end-to-end verification.
