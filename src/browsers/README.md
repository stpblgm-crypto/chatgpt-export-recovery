# Browser providers

Browser providers discover a local profile, create a private snapshot of its
cookie database, choose the active ChatGPT/OpenAI cookie partition, write a
temporary Netscape cookie jar, and clean up all session material.

`firefox.sh` currently supports the Linux Firefox layouts used by regular,
Snap, and Flatpak installations. Only Linux Firefox and Firefox Snap have a
verified real-world recovery run. Flatpak path discovery is implemented but is
not yet verified end to end.

Future Chromium-family providers must use the operating system's supported
keyring integration. They must not bypass browser encryption or persist
decrypted cookies.
