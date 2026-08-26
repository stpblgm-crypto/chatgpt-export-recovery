# Architecture

The current implementation separates session acquisition from transfer state.
The signed URL is data passed at runtime, never repository configuration.

## CLI

`src/chatgpt-export-recover` parses the URL source, output path, browser, and
segment sizes. It creates a mode-0700 runtime directory, installs signal/exit
cleanup, selects a browser provider, and calls the download engine.

## Browser/session provider

`src/browsers/firefox.sh` covers these provider responsibilities:

- detect the browser or a supported profile layout;
- discover bounded candidate profiles;
- prioritize a database opened by a running Firefox process;
- snapshot `cookies.sqlite` with a four-second online-backup limit;
- fall back to a private DB/WAL/SHM copy;
- select one Firefox `originAttributes` partition;
- filter to valid ChatGPT/OpenAI cookie domains;
- build a mode-0600 temporary curl cookie jar;
- remove temporary session material.

Those steps intentionally remain one security-scoped provider operation: cookie
values never cross into CLI diagnostics or download-engine arguments other than
the temporary jar path.

Future providers should expose the same lifecycle: detection, bounded profile
discovery, active-profile selection, session extraction, temporary jar
creation, and cleanup.

## Download engine

`src/lib/download.sh` has no browser-database knowledge. It receives a URL,
output path, cookie-jar path, user agent, private work directory, and segment
sizes. Its state machine is:

1. inspect an existing final file and `.part` checkpoint;
2. request the next byte range into an untrusted temporary segment;
3. validate status, `Content-Range`, body length, and stable remote total;
4. confirm that the checkpoint did not change concurrently;
5. append, verify the resulting size, and synchronize the filesystem;
6. repeat from the new exact size;
7. require complete size and ZIP integrity before final rename.

A failed validation never enters the append step. An append-size failure is
rolled back to the previous checkpoint size.

## Archival baseline

`legacy/CHATGPT_EXPORT_FIREFOX_RECOVER_V6_20260826.sh` preserves the proven V6
logic for auditability. The only deliberate content transformation is replacing
the original credential-like signed URL with
`__CHATGPT_EXPORT_SIGNED_URL__`. It is not the recommended entrypoint.
