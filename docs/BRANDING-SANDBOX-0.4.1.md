# Red Lotus 0.4.1 implementation and acceptance

Date: 2026-09-06. Local deployment candidate; no public release or signing.
This continues the approved management, resizing and clutter work in 0.4.0.

## Task status

- [x] Preserve dirty work and older release installers/archive.
- [x] Remove current visible edition branding and reuse the retro deck in Bridge.
- [x] Keep stable app IDs, data paths, SQLite store and internal protocol.
- [x] Rename the helper to RedLotus.ScanHelper and logs to redlotus.log.
- [x] Rename only the Sony test playlist to Red Lotus Check after verified backup.
- [x] Pin Sandbox to exact version/hashes and copy installers locally in guest.
- [x] Distinguish permissions, script policy, App Control, Defender and process errors.
- [x] Test current resources, actual Electron, host installs and upgrades.
- [x] Package both 0.4.1 installers with notices, checksums and verified ZIP contents.
- [x] Create a separately owned, powered-off Hyper-V acceptance VM.
- [ ] Complete registered Windows evaluation download and normal guest setup.
- [ ] Verify clean guest with working Defender; save its clean checkpoint.
- [ ] Complete clean-guest install/upgrade/media and real elevated-helper acceptance.
- [ ] Owner verifies Sony firmware playlist display and listening.

## Implementation

The products are **Red Lotus Player** and **Walkman Bridge**. Bridge uses the
shared retro listening deck while retaining separate Listening, Walkman and
Transfer views, persistent transport and expandable controls. Current titles,
fallback artwork, executable/installer/shortcut names, helper and logs use the
current branding. Stable installation IDs, data locations, internal protocol,
cache keys and historical evidence names retain compatibility.

The historical 0.4.0 verifier remains intact. New `packaging/verify-release.py`
checks current source, inventories, helper, ASARs and artifacts. CI selects exact
versioned installers. No source reset, commit, push or publishing occurred.
The dirty-source baseline is
`../implementation-evidence/branding-sandbox-baseline-20260906-155330.zip`.

## Current verification

| Check | Result and evidence |
| --- | --- |
| Backend | 241 passed; one environment-dependent symlink skip; includes real ffmpeg/Java private-fixture tests |
| Frontend logic | 36 passed |
| Electron/packaging logic | 28 passed; staging safety 4 passed |
| Native helper | 9 passed; published startup-hook injection rejected; `scanner-helper/artifacts/hardening-proof-041/published-startup-hook.json` |
| Actual Electron playback/management | Passed; `frontend/test-output/renderer-041-final/renderer-smoke.json`, `management-041-final/result.json` |
| Resizing | 196 cases, zero failures/errors; `frontend/test-output/resize-041-final/report.json` |
| Actual lifecycle and DSP | Passed; `packaging/build/branding-041/electron-lifecycle.json`, `dsp-offline.json` |
| Frozen package resources | Bridge 1,869 and Player 1,712 files, source/ASAR/helper parity, prior installers preserved; `packaging/build/branding-041/artifact-proof.json` |
| Packaged media services | Both pass real Defender, authenticated API, metadata/playlists, restart/cleanup with bundled runtimes. Bridge transfer/native CRUD uses private Sony backup copy; `packaging/build/branding-041/packaged-management.json` |
| Player upgrade | Data/IDs/settings/history preserved, shortcut correct, cleanup passed; `packaging/build/upgrade-041/player-d093cc1aec30482ca520a2e90c86d26c/result.json` |
| Bridge upgrade | Same checks plus renamed executable/shortcut; `packaging/build/upgrade-041/bridge-a566ffaaf42846068bf41620c930f23f/result.json` |
| Fresh host Player installation | Install/start/drain/uninstall/cleanup passed; `packaging/build/install-smoke/player-c71dce0220e942fbaf32c717d85f42ac/result.json` |
| Fresh host Bridge installation | Same checks passed; `packaging/build/install-smoke/bridge-ff16309c848b4325b7960e00f501f24a/result.json` |
| Host postflight | Explicit registry read succeeded, no product registrations remain; `packaging/build/branding-041/host-postflight.json` |

The upgrade fixture deliberately leaves an import unscanned. Reopening changes
queued to needs_scan as intended; every other queue field, playlist, setting,
terminal job and original/cache hash remains identical. An earlier fixture
assertion expected queued and was corrected to require safe recovery.

Independent review closed harness defects: success now follows verified cleanup,
environment restoration runs on cleanup failure, and registry errors cannot mean
absent installation. Hyper-V validates its owned disk chain and currently attached
media. See `security/BRANDING-0.4.1-REVIEW.md` for exact scope and evidence limits.
Production scan policy and its security boundary were unchanged by the rename.

## Environment limits

The final Sandbox run is
`packaging/build/windows-sandbox/20260906T201604184Z-d9e5ec2a`.
Both current hashes matched. Restricted PowerShell policy blocked
guest_script_launch before installers ran; WinDefend was stopped. The token had
administrator membership. No matching App Control events were observed in this
run, unlike the archived 0.2.0 installer rejection. No policy changes or bypasses
were used. Seven focused harness tests passed. See `security/SANDBOX-0.4.1.md`
and the run's `verification-summary.json`.

Daybreak's independent bounded static review found no concrete security issues
in the Sandbox harness and confirmed the blocked-run reporting. It did not run
installers or certify guest acceptance. Report:
`security/SANDBOX-DAYBREAK-0.4.1.md`.

**Red-Lotus-Windows-Acceptance** is off with Gen2, 4 CPUs, 4–8 GiB dynamic RAM,
64 GiB disk, Secure Boot and vTPM. Existing VMs are unchanged. Microsoft
registration is awaiting the user; no Windows installation or clean checkpoint
exists. Startup requires 6 GiB free host RAM. See `HYPERV-ACCEPTANCE.md`.

The live helper attempt returned elevation_cancelled for both files, zero
claims/results: `scanner-helper/artifacts/real-uac-proof-041/real-uac-smoke.json`.
Ordinary-permission Defender success and native tests do not establish successful
real UAC clearance. Clean-guest and elevated-helper acceptance remain open.

## Sony and deployment

Fresh backup: `hardware-proof/branding-0.4.1/state/backups/2026-09-06-160309`.
Only playlist 4's name changed to **Red Lotus Check**, preserving membership 11,1.
Only `OMGAUDIO/03GINF22.DAT` changed; audio and other files are unchanged.
Proof: `hardware-proof/branding-0.4.1/result.json`. Owner firmware/listening checks
remain pending.

Instructions: `packaging/DEPLOYMENT-0.4.1.md`.
Bundle: `dist_electron/deployment/Red-Lotus-Audio-0.4.1-Windows-x64.zip`.
It contains both installers, checksums, notices and sanitized verification.
Artifacts remain unsigned. The bundle does not claim clean-VM or real UAC
acceptance. Public distribution and remaining corresponding-source/notice work
are separate release actions.

ZIP SHA-256: `0e7b18839a620be711694551744ebc5afa9e3d734e48461334ffa139a1436743`
(455,134,499 bytes). Every entry was rehashed after archiving. Proof:
`packaging/build/branding-041/deployment-proof.json`.

## Environment follow-up — 2026-09-07

The official Windows evaluation ISO is now downloaded and hash-verified. The
dedicated Hyper-V acceptance VM has verified installer and private setup media
attached and remains powered off. The host preparation/coordinator/ACL checks
pass; Windows installation, clean-guest acceptance and real UAC clearance remain
pending. See [ENVIRONMENT-20260907.md](ENVIRONMENT-20260907.md) for actual evidence,
the corrected Hyper-V permission validator, and registration/memory prerequisites.
This follow-up does not change or replace the frozen 0.4.1 deployment bundle.
