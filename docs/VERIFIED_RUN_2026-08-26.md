# Verified run: 2026-08-26

This document records one completed real-world recovery run. It is provenance,
not an expected hash or size for future exports.

## Environment and mechanism

- Platform: Linux
- Browser: Firefox Snap
- Profile layout: `~/snap/firefox/common/.mozilla/firefox/<profile>/`
- Session: live Firefox database snapshot including WAL-aware fallback
- Transfer: authenticated HTTP Range, verified segmented download, checkpointed
  append
- Base segment size: 128 MiB

Every accepted segment had HTTP `206`, a matching `Content-Range` start, an
exact body length, and the same remote total. Each accepted append was followed
by a filesystem sync and a new exact checkpoint.

## Result

- Final bytes: `26798808437`
- ZIP integrity test: PASS
- Final archive SHA256:
  `dd54552c0fa87019da6d81755d66d38f4f5b74be783bfce1742ef2ca3b65fd41`

The final SHA256 identifies only that historical export. It must not be used as
an expected value for another account or export request.

## Source provenance

- Original local baseline:
  `CHATGPT_EXPORT_FIREFOX_RECOVER_V6_20260826.sh`
- Original SHA256:
  `52cacbb8bbb6b704a560d99e25ea31202bf34716a64f333d2449c08b5cd08667`
- Sanitized archival SHA256:
  `188716cba4e0435fb7c3bc82d4e8ec538af2de7f0fcced423cd325b43513c3dc`

The original file is not committed because it contains a real signed export
URL. The archival file was produced by replacing exactly that URL value with
`__CHATGPT_EXPORT_SIGNED_URL__`; all other bytes are preserved. Reapplying that
single transformation to the local source was independently compared with the
archival file before repository creation.
