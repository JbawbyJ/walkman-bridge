# Management, native Sony playlists, layout and deployment

User-authorized scope: file management and playlists, including the Sony's own
playlist menu; resizing repairs; subsequently added clutter cleanup; physical
Sony testing after a complete verified backup; package updated Windows products
for deployment. The retro Red Lotus identity and strict scan/device boundaries stay.

- [x] Preserve current dirty source and 0.3.0 installers.
- [x] Identify connected Sony and make complete hash-verified backup.
- [x] Implement local named playlists, ordered playback, membership editing,
  library removal and editable music details without changing originals.
- [x] Implement and independently verify native Sony playlist read/write while
  preserving pre-existing Sony metadata and playlists across track operations.
- [x] Fix viewport resizing, scaling and clipped controls in both apps.
- [x] Reduce clutter using contextual views, collapsed optional controls and a
  dedicated music manager; keep primary playback controls reachable.
- [x] Run focused and integrated backend, actual Electron and real mock tests.
- [x] Independently review new filesystem, IPC/API, persistence and engine changes.
- [x] Perform bounded tests on the connected Sony after all device-write gates pass.
- [x] Build, verify and install/uninstall both versioned 0.4.0 packages locally.
- [x] Prepare deployment bundle with checksums, notices, instructions and evidence.

Baseline: `../implementation-evidence/management-resize-baseline-20260905-194625.zip`.
Physical backup: `hardware-proof/pre-playlist-backups/2026-09-05-194948`, 81 manifest
entries (69 files) verified, 14 OMA tracks; identity and manifest in `hardware-proof/playlist-prewrite-backup.json`.
No test may alter this backup. Root alone owns physical device operations.

Native-engine feasibility is grounded in the existing Sony table readers/writers
and an independent implementation, not a guessed table or a self-confirming mock.
Existing private Sony tables and raw retained metadata must survive. Verify copied
fixture outcomes before a physical write, and record any hardware result honestly.

Installers remain unsigned unless a supported signing identity is supplied.
Preparing local deployment artifacts is authorized; public release/upload and
Windows security-policy changes are separate actions. Preserve all originals and
existing versions. Human firmware playback/menu confirmation is recorded separately
from database roundtrip proof.

Physical API/Defender/ffmpeg/Java roundtrip passed. The generated test tone was
removed; all 14 original OMA hashes and private Sony files were verified. Native
Unicode create/read/rename/reorder/delete passed. `Night Ops Check` remains with
two existing songs for human firmware-menu/playback acceptance, which is pending.
Evidence: `hardware-proof/management-0.4/result.json`.
