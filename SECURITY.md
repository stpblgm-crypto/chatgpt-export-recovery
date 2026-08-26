# Security policy

## Sensitive material

Treat a ChatGPT export signed URL, browser cookies, browser profile databases,
and the downloaded export itself as sensitive. Do not attach them to an issue,
commit them, or paste them into logs.

The program takes a private snapshot of the selected Firefox cookie database,
writes only ChatGPT/OpenAI cookies to a mode-0600 temporary jar, and removes the
runtime directory on normal exit and handled signals. Cookie values and the
signed URL are deliberately excluded from diagnostics.

For the least shell exposure, omit `--url` and use the hidden interactive
prompt. Command-line arguments may be retained by shell history or visible in
process listings.

## Reporting a vulnerability

Do not include live credentials or exports in a report. For this private
repository, contact the repository owner through an already trusted channel or
use a private GitHub security advisory if that feature is enabled. Include a
minimal synthetic reproducer.

## Supported security fixes

Security fixes are applied to the current default branch. The archival script
under `legacy/` exists only for provenance and is not the recommended entrypoint.
