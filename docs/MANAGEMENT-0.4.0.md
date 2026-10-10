# Night Ops 0.4.0 — management, native playlists and layout

This release includes the requested clutter cleanup as part of the deployment
scope. It extends the maintained React/Python/Electron products and Java device
engine while retaining the 0.3.0 metadata, artwork and public-link import work.
Source changes and earlier installers were preserved; no commit, push or public
release was made.

## Delivered behavior

- **Manage music:** search/sort managed files, edit title/artist/album/genre/year/
  track, select multiple files, remove managed copies with confirmation.
- **Local playlists:** create, rename, order, add/remove members, delete, play in
  order, restore paused, and stage selected playlist music for Walkman transfer.
  Playlist removal retains audio; file removal prunes all saved memberships.
- **Sony playlists:** native TREE22/GINF22 CRUD with ordered existing-device
  tracks. Names use bounded Base64/UTF-8 transport to survive the Windows Java
  launcher. Existing repeated members and opaque Sony metadata are retained.
  Native table pairs use staged replacement, rollback and incomplete-journal
  refusal; API mutations recheck volume identity and ETags under the device gate.
- **Layout:** Listening/Walkman/Transfer contexts, optional controls collapsed,
  secondary queue actions and job details expandable, persistent footer
  transport, a dedicated manager, responsive scrolling and bounded dialogs.
- **Metadata:** edited details persist independently of source bytes and cause
  transfer derivatives to be regenerated and scanned on their next use.
- **Packaging:** separate 0.4.0 NSIS installers, bundled dependencies, matching
  Java source snapshot and notices, CI management/resize acceptance added.

Local playlists do not automatically create Sony playlists. Transfer music,
then open Manage music → Sony playlists and select its device tracks. Entire
YouTube/SoundCloud playlist acquisition remains outside this version.

## Verification

| Gate | Result and evidence |
| --- | --- |
| Backend | 241 passed, one unavailable Windows symlink skip, one existing Starlette/httpx deprecation warning; includes real ffmpeg/Java mock and real Sony-backup-copy API tests |
| Frontend logic | 36 passed, including paused playlist restoration, stale saved scope, order/repeat/advance and asynchronous Stop/lease cases |
| Electron broker/lifecycle logic | 25 passed; the test harness was updated to provide the newly used Electron screen API |
| Packaging logic | 2 Node and 4 Python tests passed |
| Java engine | 28 tests passed; independently rerun against the frozen JAR, with external Windows Unicode and junction rejection checks |
| Actual Electron UI/audio | Management, renderer playback, metadata/artwork/link forms, lifecycle/window controls and OfflineAudioContext DSP harnesses passed; fixtures do not claim live provider/hardware behavior |
| Resize/scale | 196/196 final actual Electron cases, zero errors; `frontend/test-output/resize-release-040/report.json`; earlier nested-dialog clipping: 8/8 |
| Resource identity | 1,869 Bridge and 1,712 Player inventory entries verified, backend/frontend/helper byte parity, exact ASAR production modules, Java JAR/source parity; `packaging/build/management-upgrade/artifact-proof.json` |
| Host installation | Both 0.4.0 NSIS install → authenticated Electron smoke → drain → uninstall passed in isolated workspace directories; `packaging/build/install-smoke/bridge-83965e61397a442db78d0d6551efa544/result.json` and `player-8f472ec5b24e4020b3ec80a88f56a1c8/result.json` |
| Bundled runtime management | Both packaged Python backends passed actual authenticated HTTP, real Defender WAV import, metadata/local playlist edits, restart restoration and managed-file cleanup on System32-only PATH. Bridge also passed real transfer/derivative clearance and native Unicode/repeated-member CRUD on a private Sony backup copy; `packaging/build/management-upgrade/packaged-management.json` |
| Independent reviews | `security/MANAGEMENT-0.4-REVIEW.md` and `security/MANAGEMENT-BACKEND-0.4-REVIEW.md`; no remaining material findings within their stated scopes |

The Java review did not approve its author's Python changes; a separate reviewer
checked those backend changes. Review reports identify exact source hashes and
their limits. No remote CI run is claimed.

## Connected Sony evidence

The physical Sony was bound to its Windows volume identity. Before any write,
the entire device was copied and hash verified: 81 manifest entries including
69 files and 14 OMA tracks. The untouched backup is at
`hardware-proof/pre-playlist-backups/2026-09-05-194948`; its identity and manifest
are in `hardware-proof/playlist-prewrite-backup.json`.

Production authenticated API → actual Defender → ffmpeg → Java transferred one
generated harmless tone, with edited music details read back from the Sony.
Both the source and generated transfer derivative received explicit Defender
clearance. Native Unicode playlist create/read/rename/reorder/delete passed.
Only the generated track and temporary playlist were deleted; all 14 original
OMA hashes and the private Sony files match the backup. JSymphonic's ordinary
database backup remains on the device.

The postflight probe initially stopped because its allowed-file list omitted
the documented `02TREINF.DAT` duration update. A separate read-only inspection
proved the differences were confined to permitted duration bytes, verified the
entire backup and original music again, and closed the probe without repeating
any device writes. This history is retained in
`hardware-proof/management-0.4/result.json`.

**Night Ops Check** remains on the Sony, containing existing tracks 11 and 1:
風の回廊, then いつも何度でも. Firmware menu display and listening are awaiting
the user's check; API/database success does not establish those results.

## Artifact identities and acceptance limits

| Installer | Bytes | SHA-256 |
| --- | ---: | --- |
| Red-Lotus-Player-Setup-0.4.0-x64.exe | 208179865 | `393dad9bfb124d4e34760ead3ebf610f5eb37d4ebadbd863d7cd4101443f1b47` |
| Walkman-Bridge-Night-Ops-Setup-0.4.0-x64.exe | 246934770 | `d24f785e5b75be81458f27e1580a4846e88fce51664d6ee682e0b6c36e0f4076` |

The reviewed Java JAR hash is
`cbab7deae5baed02b799f43a454095e5b8b8d00a26151cc0c8649c2cccbcfc72`.
Its complete source is locked in
`packaging/sources/jsymphonic-nightops-0.4.0-src.zip`; the recorded Git commit is
the base commit, not a claim that the local changes were committed.

The installers remain unsigned local deployment artifacts. Previous clean
Windows Sandbox attempts were blocked by Application Control and lacked usable
Defender; this release does not claim that environment passes. Windows security
policy was not weakened. Human listening/menu acceptance and broader clean-host
coverage remain open. Ordinary Java audio add/delete is still non-atomic and
reports uncertain writes for reconciliation; no power-loss certification is
claimed. Public distribution remains a separate action, including signing and
the complete FFmpeg corresponding-source/native-dependency notice work described
in `packaging/NOTICES.md`.

Deployment bundle: `dist_electron/deployment/Night-Ops-0.4.0-Windows-x64.zip`.
It contains both verified installers, checksums, installation/use instructions,
notices and a verification summary without private library contents. The ZIP
checksum is stored beside it. Older 0.3.0 installer hashes were rechecked and
remain unchanged.
