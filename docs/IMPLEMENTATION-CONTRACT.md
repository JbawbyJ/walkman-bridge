# Night Ops implementation contract — 2026-09-05

The user's approved plan in this task controls the work. Two independent Windows products share source: `bridge` (Concept C, includes Walkman) and `player` (Concept A, no Java/device requirement). Both use the existing official outlined lotus, self-hosted Red Lotus fonts, strict Defender clearance, persistent playback queues, and optional audio DSP. This supersedes the old no-restyling and no-authentication design notes. Authentication is a per-launch capability, not user accounts.

## Ownership and execution

- Root: API/app factory, media store/service, authentication, streaming, conversion, Electron lifecycle integration, packaging/CI/docs, integrated tests.
- Device worker: `backend/operations.py`, `device.py`, `jobs.py`, `jsymphonic.py`, and their focused tests. Do not edit main.py.
- Daybreak: `backend/scan.py`, `backend/scan_bridge.py`, scanner unit tests, `scanner-helper/`, `electron/scanner-broker.cjs` and broker tests. Do not edit main.py/main.cjs/package manifests.
- UI worker: `frontend/` except dependency installs/lockfile changes without notifying root. No backend/Electron edits.
- All workers: you are not alone; preserve others' work, do not revert baseline changes. No commit/push/publish or actual Walkman operation. Normal dependency setup under this workspace is authorized when needed; follow sandbox escalation rules. Use fake scanner/shim and temporary volumes for tests. A fake must be injected through test code, never a release runtime environment bypass.

## Product and HTTP contract

`GET /api/health`: `{ok,version,product:'bridge'|'player',java,jar,device_connected}`. Electron preload exposes `product`, `minimize/maximize/close`; product also comes from health. Static assets are common; frontend chooses the product composition. Version target 0.2.0.

API requests use same-origin cookies; native/main-only requests use `X-NightOps-Token`. Root supplies authentication middleware. Root enforces all resource/body limits. Errors use FastAPI `{detail: string | {code,message}}`. All network code stays in frontend/api.js.

`GET /api/queue` -> `{items: MediaItem[], quota:{used_bytes,limit_bytes}}`.
`MediaItem` -> `{id,name,title,artist,album,duration_seconds,mime,size_bytes,status,scan,stream_url}`. status: `queued|scanning|awaiting_permission|blocked|failed|ready|needs_scan`. stream_url is `/api/media/{id}/stream`; media IDs are opaque. scan contains `ok,state,reason,reason_code,defender_status` and technical fields for backend only. Missing/unknown values must display honestly. No arbitrary filesystem paths in renderer media records.

`POST /api/media/import` multipart `files` -> `{job_id}`; scans/caches without requiring a Walkman. `POST /api/media/{id}/rescan` -> `{job_id}`. `DELETE /api/queue/items/{id}` removes one item. `PATCH /api/queue/order` JSON `{ids:[...]}` requires an exact permutation. `POST /api/media/{id}/prepare` JSON `{format:'flac'}` requests lossless playback fallback and returns `{job_id}`. Only request fallback after native audio codec failure.

`GET/PATCH /api/playback-state` persists `{media_id,position_seconds,volume,shuffle,repeat}`. `POST /api/playback/lease` with `{media_id}` leases the selected track before assigning an audio source; `DELETE` with the same body releases only that selected ID. The renderer serializes lease mutations and preserves explicit play/stop intent across asynchronous preparation. Main-only `POST /api/internal/playback/release` releases abandoned playback only after Stop acknowledgement or confirmed renderer death. Playback leases count toward shutdown busy state but do not disable ordinary queue/import controls.

Per-media processing admission is exclusive: a second overlapping prepare/rescan/transfer returns409. Persisted uncertain jobs propagate their flag into media records at restart. Rescan clears the selected playback derivative as well as the source. Cache quota includes interrupted physical artifacts; startup removes only unreferenced managed copies.

`POST /api/transfers` JSON `{media_ids:[...]}` -> `{job_id}`; bridge only. Legacy `/api/upload` remains upload+transfer, subject to the same strict gate; no scan-off argument. Player has no device mutation routes.

`GET /api/jobs/{id}` keeps existing fields and adds `{kind,phase,needs_reconcile,files:[{file_id,name,media_id,state,detail,reason_code,scan}]}`. `GET /api/jobs/latest` -> job or null. Terminal job statuses: `done|partial|failed|interrupted`; file state: `queued|scanning|awaiting_permission|blocked|transcoding|ready|transferring|transferred|failed|interrupted|unknown`. Unknown means verify device state, never automatic retry. UI treats log_tail as display only.

`GET /api/device` preserves current schema. `GET /api/tracks` keeps array and adds ETag response header. `DELETE /api/tracks/{id}` requires If-Match (missing428/stale409). `POST /api/backup` preserves `{ok,path}`; progress visible through operation snapshot. `GET /api/engine-busy` -> `{busy,draining,pending,active:[{kind,job_id}]}`. `POST /api/shutdown/drain` returns snapshot immediately; no new admissions after drain. Close must not kill admitted work; playback streams are stopped first.

## Device/jobs Python seams

`device.DeviceIdentity(mount:Path,volume_id:str)`; `capture_device_identity(mount)->DeviceIdentity`; `revalidate_device(identity)->Path` raises on mismatch. `backup_device(mount,dest_root=None)->Path` copies/verifies/publishes only complete backups. Root calls backup inside coordinator device_session.

`operations.OperationCoordinator.admit(kind,device=None,job_id=None)->ticket`; reject drain with `DrainingError`; reject a concurrent `import` with `BusyError`. `device_session(ticket)` context manager revalidates identity under one device gate. `finish(ticket)`, `begin_drain()`, `snapshot()`, `wait_drained(timeout=None)`; no lifecycle lock held across blocking work. Lock order: short lifecycle bookkeeping released -> device gate -> private shim lock. No reverse jobs-lock-to-device path.

`JobStore.create(job_id,total_files,kind='upload')->Job`; existing log/status/touch methods stay. Add `Job.set_phase(phase)`, `Job.set_files(list_of_dicts)`, `Job.update_file(file_id,**changes)`, `Job.to_dict()`, `JobStore.latest()`, `JobStore.recover_interrupted()`. File rows and phase must persist in one transaction when practical. Preserve existing jobs, add interrupted status and needs_reconcile. Default database honors WALKMAN_JOBS_DB; root config sets app-data directory before import.

## Strict scanner integration

`scan.scan_audio(path:Path)->ScanResult` remains the primary API. Result has `.ok`, `.state`, `.reason`, `.engine`, `.pid`, `.reason_code`, `.defender_status`, `.to_dict()`. Only explicit Defender clean + valid format sets ok true. `scan.clearance_valid(path:Path,record:dict)->bool` binds hash/size/policy/version/time; unavailable signature status invalidates clearance. `scan.snapshot()` returns honest scan rows and capability status. No production environment/UI bypass. Tests inject boundary callables/monkeypatches.

`scan_bridge` owns pending elevation requests and waiting/results. Daybreak must publish its exact API early to root; pending/result endpoints are MAIN-ONLY (header token required, cookie alone insufficient). Worker must bind result to content digest, nonce, request identity and expected helper process; renderer cannot approve a result. `electron/scanner-broker.cjs` integrates into main via an exported start/stop broker factory; root will supply base URL, token, helperPath, cacheRoot, logging callback. Fixed helper commands, private named-pipe IPC; no permanent service. Native helper source targets self-contained .NET10 win-x64.

## Quality and storage

Preserve cleared sources, lossless FLAC fallback only for unsupported playback codecs. Walkman conversion remains 192k/44.1kHz/stereo MP3. Scan all generated derivatives. DSP is playback-only and defaults bypassed: ten-band EQ, conservative presets, loudness matching, headroom/output limiter; real waveform/spectrum, reduced motion.

Defaults: 500MiB/file, 2GiB/batch, 200 files, one import batch, 10GiB cache. Only unreferenced artifacts can be evicted. Queue and active playback/transfer hold leases. Never delete original source files. New media tables use the same per-product SQLite file as jobs. Native selection and drag/drop copy to managed storage; no remote URLs or indexed library.

## Acceptance

Focused failing regression tests precede changes where practical. Full backend and frontend tests, helper/broker tests, real Electron playback and close tests, real ffmpeg+JAR mock roundtrips, and two packaged smoke/install checks. Real Defender tests use harmless generated audio only; malware semantics use mocks. Physical Walkman proof is human-controlled after backup. Preserve evidence and report blockers accurately; do not call mock clearance real Defender verification.
