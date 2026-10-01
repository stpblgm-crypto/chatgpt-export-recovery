# Opera Range Bridge

Local-only helper for the existing ChatGPT export recovery tool.

Purpose:
- make HTTP Range requests through the real Opera/Chromium network stack;
- save each verified-size range as a separate local chunk;
- resume by chunk index without re-downloading verified ranges;
- never commit the signed URL.

The runtime copy lives under ~/.hydra_private and contains config.js with the signed URL.
The repository only stores config.template.js.

Current flow:
1. Load the runtime directory as an unpacked Opera extension.
2. Extension automatically downloads a 1 MiB canary at the configured offset.
3. Compare that canary byte-for-byte against the same offset in the existing local prefix.
4. If it matches, signal startTail and download the remaining export in fixed chunks.
5. Local assembler concatenates the existing prefix + downloaded tail chunks and runs ZIP/SHA256 validation.
