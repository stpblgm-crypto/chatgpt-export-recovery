# Security policy

## Sensitive material

Treat a ChatGPT export signed URL, browser cookies, browser profile databases,
and the downloaded export itself as sensitive. Do not attach them to an issue,
commit them, or paste them into logs.

The preserved Linux/Firefox shell program takes a private snapshot of the selected Firefox cookie database,
writes only ChatGPT/OpenAI cookies to a mode-0600 temporary jar, and removes the
runtime directory on normal exit and handled signals. Cookie values and the
signed URL are deliberately excluded from diagnostics.

For the least shell exposure, omit `--url` and use the hidden interactive
prompt. Command-line arguments may be retained by shell history or visible in
process listings.

## Python local link input

The default Python transport uses standalone HTTPS links without reading browser
profiles or cookies. Its default terminal prompt is hidden. Explicit clipboard input reads
once without modifying the clipboard; clipboard managers may retain data under
their own settings.

The optional browser input binds only to `127.0.0.1` on a random port. It uses a
random one-time path, an independent session code, exact Host and Origin checks,
a masked field, bounded requests, and a five-minute timeout. It serves no files,
does not log requests or export links, and keeps the submitted link in memory.
Check that the displayed computer and session code match the terminal before
pasting. This protects against unrelated web origins, not software running as
the same local user. A private destination directory is still required.

Neither input mode bypasses login, anti-bot challenges, encryption, or server
Range restrictions. HTTP 401/403 and non-206 responses leave verified bytes
unchanged. Never commit runtime checkpoints, input links, cookies, or exports.

The separate Linux-only `--firefox-profile` option explicitly reads unexpired
first-party ChatGPT cookies from one selected Firefox profile into memory. The
database is opened read-only; no snapshot or cookie file is created. Other
domains, containers, and partitioned cookies are excluded before values are
loaded. Credentials are sent only to HTTPS `chatgpt.com` on port 443, respecting
cookie scope; redirects to storage receive no Firefox cookies. Server cookies
are not adopted. The in-memory jar is cleared when the transport closes. This
does not protect against memory inspection by another process running as you.

## Reporting a vulnerability

Do not include live credentials or exports in a report. For this private
repository, contact the repository owner through an already trusted channel or
use a private GitHub security advisory if that feature is enabled. Include a
minimal synthetic reproducer.

## Supported security fixes

Security fixes are applied to the current default branch. The archival script
under `legacy/` exists only for provenance and is not the recommended entrypoint.

