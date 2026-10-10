# Project structure — two Windows products

| Path | Responsibility |
| --- | --- |
| electron/main.cjs | Product state directory, backend lifecycle, session/origin/IPC boundary, window and drain |
| electron/preload.cjs | Narrow window controls and acknowledged playback stop |
| electron/scanner-broker.cjs | Main-only authenticated batch elevation exchange |
| backend/boot.py | Reserved loopback socket and signed port announcement |
| backend/application.py, main.py | Authenticated application factory and route integration |
| backend/security.py | Exact origin/capability, CSP and bounded multipart admission |
| backend/media_store.py | SQLite media/queue/playback state, managed copies, quota and leases |
| backend/media_service.py | Scan-before-consume, analysis, separate derivatives, durable per-file outcomes |
| backend/scan.py, scan_bridge.py, defender_status.py | Strict Defender clearance, elevation bindings and Windows Security Center status |
| backend/transcode.py | Bounded ffmpeg analysis and playback/transfer conversion |
| backend/operations.py | Admission, busy/drain, serialized device access and identity |
| backend/jobs.py | Additive SQLite jobs and interrupted/uncertain recovery |
| backend/device.py | Bridge-only discovery, identity and verified backup |
| backend/jsymphonic.py | Bridge-only HeadlessCli adapter; sole OMGAUDIO writer and private lock |
| scanner-helper/ | Packaged .NET10 exchange/UAC helper with ACL/OS PID verification |
| frontend/src/App.jsx, usePlayer.js, api.js | Product compositions, playback intent and API calls |
| frontend/src/dsp.js, components/ | Web Audio enhancement and accessible controls |
| frontend/src/nightops.css, retro.css | Concept C and Concept A styling |
| frontend/public/ | Official outlined lotus and locally bundled fonts |
| packaging/ | Pinned runtimes/wheels, notices, staging, two NSIS products and install smoke |
| .github/workflows/windows-products.yml | Backend/frontend/helper/engine/mock/build checks |

Both products own a separate process and nightops.sqlite beneath their application-data directory. That database contains additive job history, per-file outcomes, media records, queue ordering and playback state. Player never imports device/JSymphonic modules and its package excludes Java/JAR.

Legacy browser/pywebview launchers remain historical source and are excluded from product packaging. They do not represent the authenticated Electron entry point. The original HTML export remains an external design reference; its wrapper is never run.

Verification lives in backend/tests, frontend/src/*.test.js, frontend/tests, electron/*.test.cjs, scanner-helper tests, packaging tests and the companion Java tests. The progress document distinguishes executed evidence from CI configuration and manual acceptance gates.
