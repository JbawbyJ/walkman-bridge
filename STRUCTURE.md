# Project structure — two Windows products

Module map for the tree at this revision. Product version in `package.json` and `backend/application.py` is **0.4.1**. Behavior is in [README.md](README.md) and [docs/DESIGN.md](docs/DESIGN.md). Current acceptance evidence is [docs/BRANDING-SANDBOX-0.4.1.md](docs/BRANDING-SANDBOX-0.4.1.md).

| Path | Responsibility |
| --- | --- |
| electron/main.cjs | Product state directory, backend lifecycle, session/origin/IPC boundary, window and drain |
| electron/preload.cjs | Narrow window controls and acknowledged playback stop |
| electron/boundary.cjs | Exact-origin checks, IPC sender validation, shutdown drain loop |
| electron/scanner-broker.cjs | Main-only authenticated batch elevation exchange |
| backend/boot.py | Reserved loopback socket and signed port announcement |
| backend/application.py, main.py | Authenticated application factory and route integration (`VERSION` 0.4.1) |
| backend/security.py | Exact origin/capability, CSP and bounded multipart admission |
| backend/media_store.py | SQLite media/queue/playback state, managed copies, quota and leases |
| backend/media_service.py | Scan-before-consume, analysis, separate derivatives, durable per-file outcomes |
| backend/metadata.py | Bounded title, artist, album, genre, date/year and track text |
| backend/playlists.py | Local playlist routes and Bridge Sony playlist routes |
| backend/link_import.py, link_download_worker.py | One public YouTube or SoundCloud track, before scan |
| backend/scan.py, scan_bridge.py, defender_status.py | Strict Defender clearance, elevation bindings and Windows Security Center status |
| backend/transcode.py | Bounded ffmpeg analysis; lossless FLAC for playback, separate 192 kbps/44.1 kHz stereo MP3 for transfer |
| backend/operations.py | Admission, busy/drain, serialized device access and identity |
| backend/jobs.py | Additive SQLite jobs and interrupted/uncertain recovery |
| backend/device.py | Bridge-only discovery, identity and verified backup |
| backend/jsymphonic.py | Bridge-only HeadlessCli adapter; sole OMGAUDIO writer and private lock |
| scanner-helper/ | Packaged .NET 10 exchange/UAC helper with ACL/OS PID verification |
| frontend/src/App.jsx, usePlayer.js, api.js | Product compositions, playback intent and API calls |
| frontend/src/dsp.js | Optional ten-band playback EQ, loudness match and limiter (default bypassed) |
| frontend/src/components/PlayerPanels.jsx | Shared deck, queue, transport, equalizer and artwork |
| frontend/src/components/BridgePanels.jsx | Device summary, ledger, staging and operation panel |
| frontend/src/components/MusicManager.jsx | Manage music: files, local playlists, Bridge Sony playlists |
| frontend/src/components/LinkImportDialog.jsx | Single-track import link dialog |
| frontend/src/retro.css | Shared retro playback deck |
| frontend/src/nightops.css | Bridge Listening / Walkman / Transfer workspace |
| frontend/src/music-manager.css | Manage music dialog |
| frontend/public/ | Locally bundled Orbitron and IBM Plex fonts |
| frontend/src/assets/lotus.png | Official outlined lotus used by the deck |
| packaging/ | Pinned runtimes/wheels, notices, staging, two NSIS products and install smoke |
| packaging/build-products.ps1 | Current installer build used by `npm run dist` and CI |
| .github/workflows/windows-products.yml | Windows backend, frontend, helper, engine, mock and build checks |

`frontend/src/components/Titlebar.jsx`, `InstrumentCluster.jsx`, `Ledger.jsx` and `Strips.jsx` are not imported by `App.jsx`. They are not the mounted product UI.

Both products own a separate process and `nightops.sqlite` beneath their application-data directory (`Red Lotus Player` or `Walkman Bridge` under `%LOCALAPPDATA%`). That database contains additive job history, per-file outcomes, media records, queue ordering and playback state. Player never imports device/JSymphonic modules and its package excludes Java/JAR.

The supported entry is Electron (`electron/main.cjs`), started with `npm run electron` for Bridge or `npx electron . --product=player` for Player. `packaging/launcher.py`, `scripts/start.sh`, `scripts/setup.sh`, `scripts/setup.bat` and `START-WALKMAN-BRIDGE.bat` remain in the tree and are not that authenticated entry. `packaging/build.ps1` is also present; the workflow calls `packaging/build-products.ps1`, not `build.ps1`. Whether anything outside CI still runs `build.ps1` was not rechecked. The original HTML export remains an external design reference; its wrapper is never run.

Verification lives in `backend/tests`, `frontend/src/*.test.js`, `frontend/tests`, `electron/*.test.cjs`, `scanner-helper` tests, packaging tests and the companion Java tests. Executed 0.4.1 evidence is [docs/BRANDING-SANDBOX-0.4.1.md](docs/BRANDING-SANDBOX-0.4.1.md). Older logs are [docs/MANAGEMENT-0.4.0.md](docs/MANAGEMENT-0.4.0.md), [docs/MEDIA-UPGRADE-0.3.0.md](docs/MEDIA-UPGRADE-0.3.0.md) and [docs/archive/IMPLEMENTATION-PROGRESS.md](docs/archive/IMPLEMENTATION-PROGRESS.md). Those logs are not rerun by editing this map. Human checks are [docs/acceptance/listening-checklist.md](docs/acceptance/listening-checklist.md) and [docs/acceptance/menu-checklist.md](docs/acceptance/menu-checklist.md). `frontend/tests/walkman-smoke.test.mjs` is the headless Bridge viewport and renderer-boot check; [packaging/README-WINDOWS.md](packaging/README-WINDOWS.md) distinguishes that suite from the Windows Electron harness and the remaining manual acceptance gates.
