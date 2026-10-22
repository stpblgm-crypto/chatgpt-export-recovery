# Desktop app: 0.2.0 candidate

This is a source-distributed Python/Tk desktop application for Windows, Linux,
and macOS. Python 3.10+ and Tk are required; no third-party runtime packages,
browser extension, remote-debugging port, or account integration is required.
It has not yet been accepted on native Windows/macOS desktops or a live export.

## Launch

Download this repository's source and extract it to a local folder.

- Windows: install a Python distribution with Tcl/Tk support, then run
  `py -m chatgpt_export_recovery` from the repository folder. `launch.pyw`
  can also be opened with Python when `.pyw` associations are configured.
- macOS: use a Python distribution with Tk support and run
  `python3 -m chatgpt_export_recovery` from the repository folder.
- Linux: use Python with your distribution's Tk package (often `python3-tk`)
  and run `python3 -m chatgpt_export_recovery` in a graphical desktop session.

This repository does not install software automatically. Test your existing Tk
installation with `python -m tkinter` (use `py` / `python3` as appropriate).
There are no packaged `.exe`, `.app`, signed installers, or auto-updates in this
candidate. Building these requires separate platform build/signing work.

An optional terminal interface is available on all three platforms:

```text
python -m chatgpt_export_recovery --cli --output /your/private/folder/export.zip
```

The URL is requested through a hidden interactive prompt. URL arguments and
non-interactive stdin are deliberately unsupported to avoid shell history and
process-list leakage. Optional `--import-partial` and `--sha256` arguments do
not contain secrets. Installing the package is optional; all commands above
work directly from source without `pip install`.

If you separately install the package, `chatgpt-export-recovery-cli --output
/your/private/folder/export.zip` selects terminal mode automatically; it does
not require another `--cli` flag or import Tk. The GUI command remains
`chatgpt-export-recovery`. Both terminal forms keep the same hidden-prompt and
non-interactive-input restrictions.

## From any browser

1. Request the export in your normal ChatGPT session. Open the export email or
   download flow in that browser.
2. Copy the **actual HTTPS file download link** into the masked link field.
   Browser download managers may offer “Copy download link”; availability and
   wording vary. A landing page, `blob:` URL, or browser-session-only URL will
   not work. Do not paste a copied cURL command, cookies, or access tokens.
3. Choose a new output ZIP filename in a private local folder.
4. Optionally select a stopped browser partial, then press **Start / resume**.

Chrome, Chromium, Edge, Firefox, Brave, Opera, Safari, and other browsers can
all be the source of a user-copied direct link. This is browser independence,
not automatic extraction of each browser's session or download database.
The HTTP server must allow standalone HTTPS Range requests and supply a strong
ETag. Some ChatGPT export URLs need the browser's authenticated session. In
that case the GUI stops and explains the limitation; it does not bypass login,
anti-bot challenges, cookie encryption, or operating-system protections.

Only regular partial files are accepted. Firefox `.part` and Chromium-family
`.crdownload` files are possible inputs. Safari `.download` bundles are
folders and are not accepted directly; supply a regular payload file only if
you know which file is the actual incomplete archive. Mobile browsers are not
native app targets; the desktop app can use a compatible link obtained there.

## Resume, pause, and changed links

The output `export.zip` uses a sibling `export.zip.recovery` folder containing
`checkpoint.part`, `state.json`, and an OS-managed advisory lock. The metadata
contains counters and SHA256 digests, never the URL or raw ETag. Only verified,
fsynced segments become committed checkpoints. On restart, the local committed
prefix is hashed before downloading further bytes. A suffix left by a crash
between data and metadata commits is discarded only after the committed prefix
passes its hash check. A missing/corrupt metadata file fails closed.

**Pause** or **Pause and close** preserves committed segments. A blocking
network read can take up to its configured timeout (20 seconds by default);
retries and ZIP verification also check for cancellation. Restart the app,
paste the link, and choose the **same output filename** to resume.

An expired link produces a clear stop without appending an error response.
Use a fresh direct link for the same export. Its host/path, strong ETag, and
size must match the checkpoint. Changes to query signatures are permitted;
identity depends on a server honoring strong ETag semantics for that resource.
A new export or a redirect-only link with a changed initial path needs a new
output. A trusted expected SHA256 is an additional independent integrity check;
re-enter it on each resume if using this option.

Imported browser partials have no trusted app metadata. Every imported byte
is compared with a fresh server Range response before adoption. This costs
network traffic equivalent to the imported prefix, but prevents blindly
combining a wrong or corrupted browser partial with another export. The source
file is opened read-only and never renamed or deleted. Stop the browser's
original download before importing. Once imported segments are committed,
ordinary app resume avoids fetching them again.

## Final verification and storage

The complete file must have the exact remote size, match the incremental and
full-file SHA256, pass an optional expected checksum, and pass every ZIP entry's
CRC. No archive members are extracted, so malicious ZIP paths cannot write
outside the destination. ZIP validity is not proof of the sender's identity,
of ChatGPT's content schema, or of a non-malicious archive.

Publication uses an atomic, no-overwrite hard link. Local NTFS, APFS, and ext4
normally support it; FAT/exFAT and some network or virtual filesystems do not.
On unsupported storage the verified checkpoint is retained and publication
fails with guidance. POSIX payload files are created with mode 0600; Windows
access follows the destination folder's ACL. Use a private local folder.

The completed ZIP and recovery checkpoint refer to the same underlying data;
this does **not** require a second full-size copy. Editing either changes both.
After closing the app you may manually delete the recovery folder, keeping the
final ZIP. No automatic deletion of user files occurs. Existing final files
are never overwritten. Two jobs targeting one output cannot run concurrently.

No signed link, browser cookies, credentials, or archive content is logged.
The URL exists in memory while working and is cleared from the GUI on success
or close. This does not defeat memory inspection by software running as you.
The app honors system HTTPS proxy settings, like other urllib clients; use a
trusted network and proxy configuration. No telemetry or upload feature exists.

## Verification commands and acceptance

```text
python -m unittest discover -s tests -p "test_python_*.py" -v
python -m compileall -q chatgpt_export_recovery launch.pyw
```

On Linux, also run `bash tests/run.sh` for the preserved shell baseline.
The Python tests use synthetic responses only. No real account, browser
profile, signed export link, or live download is required.

Native manual acceptance remains required on each target OS:

- Open the GUI, resize it, and confirm file dialogs and keyboard navigation.
- Start a synthetic Range download; repeated Start must not spawn a second job.
- Pause, resume, close while active, then reopen and resume from the saved bytes.
- Verify clipboard content is not modified and the URL is masked by default.
- Test a private folder with non-ASCII characters and spaces; final output must
  never replace an existing file.
- Confirm final ZIP checksum, file permissions/ACL, and recovery folder cleanup.
- Test the owner's actual export only with the owner's separate acceptance.

Hosted CI is paused by the owner. This candidate does not add, trigger, or
modify a GitHub Actions workflow; local test evidence is recorded separately.
