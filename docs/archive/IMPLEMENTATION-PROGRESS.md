# Implementation progress — 0.2.0 (historical)

**Historical.** This file records the **0.2.0** baseline. It is not the current build.

The repository version in `package.json` and `backend/application.py` is **0.4.1**. Current behavior and acceptance are in [../BRANDING-SANDBOX-0.4.1.md](../BRANDING-SANDBOX-0.4.1.md). That record supersedes this file, along with [../MANAGEMENT-0.4.0.md](../MANAGEMENT-0.4.0.md) (0.4.0 manager, playlists, and layout) and [../MEDIA-UPGRADE-0.3.0.md](../MEDIA-UPGRADE-0.3.0.md) (0.3.0 metadata, artwork, and link import).

Prose paths in the body below were written when this file lived in `docs/`. Test counts, installer names, and hashes are 0.2.0 evidence only.

---

# Night Ops implementation and verification — 0.2.0

This records the original 0.2.0 baseline. The later metadata, artwork and link-import
upgrade and its 0.3.0 verification are documented in [../MEDIA-UPGRADE-0.3.0.md](../MEDIA-UPGRADE-0.3.0.md).
The 0.4.0 music manager, local/native playlists, resizing and clutter cleanup,
including the backed-up physical Sony test, are documented in
[../MANAGEMENT-0.4.0.md](../MANAGEMENT-0.4.0.md). Hardware statements below describe the
earlier 0.2.0 baseline only.

The approved two-product plan is implemented in the existing checkout. Original dirty work was preserved in ../implementation-evidence/baseline-20260905-155756.zip, with its status/diff evidence. No reset, commit, push or publication was performed.

## Delivered behavior

- Red Lotus Player (Concept A) and Walkman Bridge — Night Ops (Concept C), with the official outlined lotus, bundled Orbitron/Plex fonts, resizable Electron windows and wired controls.
- Shared source-quality playback, paused session restoration, persistent queue/order/volume/shuffle/repeat, codec fallback, actual analyser visualization and optional ten-band enhancement. Enhancement defaults bypassed and never alters the separate Walkman MP3.
- Managed media IDs/hashes, bounded import/cache, processing and playback leases, authenticated ranges, and additive SQLite media/job outcomes. Originals remain untouched.
- Strict Defender clearance before decoding or consumption, including generated FLAC/MP3 artifacts. Daybreak implemented the scanner foundation. Native helper hardening and independent security review addressed pipe identity, runtime hooks, PowerShell lookup, live protection status and backend loss.
- Batched on-demand .NET10 elevation with OS pipe process verification and content/nonce bindings. Missing/disabled/error/timeout/cancelled/ambiguous scan states fail closed. No production scan bypass.
- Device coordinator, volume identity, ETag deletion, verified partial-to-published backup, durable uncertainty and interrupted-job recovery. No automatic replay of writes.
- Stop acknowledgement, full busy/drain, crash handling and exact-origin/cookie/IPC restrictions. Playback counts toward shutdown while ordinary queue actions remain usable.
- Two versioned self-contained NSIS installers. Both bundle Python/ffmpeg/helper/fonts/notices; only Bridge bundles Java, JSymphonic and device modules. Build/CI definitions and dependency locks are included.

## Executed verification

| Gate | Result and evidence |
| --- | --- |
| Backend including real ffmpeg/Java mock integration | 154 passed, 1 environment symlink skip; packaging/build/backend-final-tests.log |
| Frontend logic and asynchronous playback/lease regressions | 27 passed; npm run test:frontend |
| Electron lifecycle/broker/boundary regressions | 25 passed; npm run test:electron |
| Native helper | 9 passed, independently repeated; wrong pipe PID sends no secret bytes; published startup-hook regression blocked |
| Java engine | 13 Maven tests passed; fresh API → ffmpeg → Java → list → verified backup → ETag delete mock round trip passed |
| Real Defender and UAC | Fresh user-requested UAC used helper PID31384 for two harmless WAVs, both CLEAN; scanner-helper/artifacts/real-uac-proof/real-uac-smoke.json |
| Actual source and derivative clearance | Real Defender → import/analysis → WAV range → FLAC conversion/clearance → FLAC range passed without device imports; packaging/build/real-media/real-media.json |
| Actual Electron playback/UI | Paused restore, decoding, seeking, queue advance/reorder, rotary control, enhancement bypass, leases/Stop and both desktop/compact compositions passed; frontend/test-output/renderer-smoke.json and PNGs |
| Actual Electron lifecycle | Window controls passed; failed busy probe retained app; close waited three busy responses then idle; packaging/build/electron-lifecycle-acceptance/proof.json |
| Actual native Web Audio DSP | Six OfflineAudioContext checks passed; bypass error0, all ten filter centers within0.000082dB, measured matching and settled limiter ceiling; frontend/test-output/audio/dsp-offline.json |
| Listening comparison artifacts | Six-second stereo24-bit original.wav/enhanced.wav in frontend/test-output/audio; no human listening judgment claimed |
| Dependency audits | Root/frontend npm and locked Python runtime audits reported no known advisories at build time |
| Installed resources | Both embedded Python3.13.15 factories and database schemas passed; Player loads no device modules; packaging/build/*-resources-probe.json |
| Source parity | Packaged backend/frontend/helper and ASAR main/preload/broker/boundary compared against final source; packaging/build/final-source-parity.json |
| Installation | Both actual NSIS install → authenticated Electron smoke → graceful exit → uninstall tested in isolated workspace directories on this Windows host; final result records under packaging/build/install-smoke |
| Clean Windows Sandbox | Guest build26100 booted with no developer tools on PATH, verified both final installer hashes, then Application Control blocked both installers before execution. No clean-guest installation success claimed; packaging/build/windows-sandbox/20260905T213005Z/results/guest-result.json |

Regression review also reproduced and fixed same-media conversion races, repeated uncertain transfers before/after restart, lost threat states, stale derivative rescans, orphan quota accounting, delayed playback restarts, removal races and stale local-origin capabilities after backend death. Security details and attribution: security/NIGHT-OPS-REVIEW-2026-09-05.md.

## Artifacts

- dist_electron/player/Red-Lotus-Player-Setup-0.2.0-x64.exe
- dist_electron/bridge/Walkman-Bridge-Night-Ops-Setup-0.2.0-x64.exe
- Each product folder contains SHA256SUMS.txt and a win-unpacked tree. Use the final SHA256SUMS/result records; preliminary build hashes are superseded.
- Final Bridge SHA-256: 04954d13e307165e6ee6bc2c795bd56d4b3b3a924b43991975ad5564d111fe9d (214709199 bytes).
- Final Player SHA-256: 0a599edfc232c37f8c340a21cfcab036498e26b53d160927e8ed73ef3e997c7b (175989540 bytes).
- Final host install/uninstall and source inventory proof: packaging/build/final-installation-verification.json, final-artifacts.json and asar-proof.json.
- packaging/README-NIGHT-OPS.md describes reproducible builds and installer probes. CI includes backend, frontend, native, Java, actual Electron/DSP, mock integration, packaging and install checks. CI was authored and locally exercised in equivalent commands; no remote run is claimed.

## Remaining acceptance and release gates

No physical Walkman was operated. NW-S705F playback and recovery require an operator-controlled hardware test after a full verified backup. Installation passed on this host with bundled runtimes. Clean Windows Sandbox execution was attempted, but its Application Control policy blocked both unsigned installers before they ran. The first Sandbox attempt failed only at WMI inventory; the corrected harness used Environment/registry metadata and recorded the actual installer policy block. Neither Windows policy nor Defender was weakened. Clean-environment installation acceptance therefore remains blocked, not passed. Human listening comparison and a broader Windows/Defender configuration matrix remain manual acceptance work.

Installers/helper are unsigned local artifacts. Publishing is a separate action. Before public distribution, complete the corresponding-source package for ffmpeg and linked GPL components, signing/provenance decisions and the additional release security review described in the security report. This work does not claim a public release, full penetration test, inter-sample true-peak limiting or completed manual acceptance.
