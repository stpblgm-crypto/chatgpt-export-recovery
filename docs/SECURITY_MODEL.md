# Security model

## Assets

- signed export URL;
- Firefox cookies and container/origin partition;
- browser database, WAL, and SHM files;
- partial and complete data-export archives;
- local filesystem paths and account context.

The first four asset classes must never enter version control or diagnostic
output. Local paths may appear in interactive diagnostics so the user can verify
which Firefox profile was selected.

## Trust boundaries

The local user supplies the URL and chooses the output path. Firefox and its
profile are local trusted inputs. HTTP response bodies and headers are untrusted
until status, range boundaries, length, and total-size invariants all pass.

The durable `.part` file is the only accepted checkpoint. A downloaded segment
is temporary and disposable. The engine checks the checkpoint size immediately
before append and checks the exact resulting size immediately afterward.

## Secret lifecycle

1. The URL arrives through a hidden prompt, environment, restricted file, or
   explicit CLI argument.
2. The URL is held in process memory and written to a mode-0600 temporary curl
   config, keeping it out of curl's argument list and diagnostics.
3. Firefox databases are copied only into a mode-0700 runtime directory.
4. The selected cookie jar, URL config, and metadata are mode 0600.
5. Exit and handled signals remove the runtime directory.

`--url` is convenient but may expose the URL through shell history and the CLI
process listing. The hidden prompt is the preferred input. A process running as
the same operating-system user may still be able to inspect process memory or
open files; this tool does not defend against a compromised local account.

## Explicit non-goals

- login, MFA, or anti-bot bypass;
- browser-encryption bypass;
- persistence of decrypted cookies;
- upload, telemetry, analytics, or external SaaS processing;
- automatic or scheduled export requests;
- protection against a hostile kernel, root user, or compromised browser.

## Repository gates

`.gitignore` excludes archives, checkpoints, segments, browser databases,
cookie jars, headers, sessions, logs, and environment files. Before publication,
the staged tree must be searched for signed-URL shapes and known source-only
markers, then checked with an installed secret scanner when available.

## Experimental Chromium boundary

Chromium session reads require authorized runtime access to the selected
profile. Only exact ChatGPT/OpenAI domains enter the filtered private snapshot.
Cookie partitions fail closed by default. Explicit unpartitioned-only
selection omits partitioned rows before optional decryption and never merges
their values into a jar. Unsupported encryption in selected cookies, missing
keys and ambiguous profiles fail closed. The optional pinned library may use the local OS keyring only
when explicitly selected; plaintext mode does not. Neither mode accesses CDP,
process descriptors, hidden browser state, or another environment's session.
Dependency diagnostics are suppressed; the jar remains mode 0600. Library
cryptography is not reimplemented here. Cleanup is best effort on normal exit
and handled signals; SIGKILL or a host crash may require removing the private
runtime directory manually. See the experimental guide for full limitations.
