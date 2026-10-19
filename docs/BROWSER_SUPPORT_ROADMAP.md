# Browser support

## Python direct-link app (0.2.0 candidate)

The same Python/Tk UI targets Windows, Linux, and macOS. Chrome, Chromium,
Firefox, Edge, Safari, Brave, Opera, and any other browser can supply a copied
HTTPS download link. This route is independent of browser databases and does
not extract credentials. The endpoint must support standalone byte ranges
and a strong ETag; authenticated/session-only endpoints are unsupported.

Regular browser partials can be imported read-only, with every byte checked
against the server. Browser bundles/directories are not imported directly.
All native desktop/platform and live-export acceptance remains pending;
synthetic engine/controller tests do not prove universal browser integration.

See [desktop app guide](DESKTOP_APP.md).

## Preserved legacy roadmap

# Browser support roadmap

## Current provider

Firefox on Linux is implemented. The verified run used Firefox Snap. Regular
Firefox and Firefox Flatpak paths are discovered, but Flatpak has not received
an end-to-end real export run.

Firefox-specific state includes `profiles.ini`, `cookies.sqlite`, its WAL/SHM
sidecars, and container partitioning through `originAttributes`.

## Next providers

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
