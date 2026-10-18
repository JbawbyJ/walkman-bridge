# HANDOFF → Hermes profile dev · MoA Dev (Heavy agg)

## Goal

Implement the user's request to “build / fix all three”: improve local music metadata and supported artwork handling, repair untagged Walkman transfer names, and add single-track YouTube/SoundCloud link import to both Red Lotus Player and Walkman Bridge — Night Ops. Extend the maintained Electron/React/Python applications in `C:\Users\rober\Desktop\Projects\walkman\walkman-bridge`. Downloaded music must enter the same managed-cache, Defender, persistent queue, playback, and Bridge transfer pipeline as selected local files. The user's later link-import request supersedes the initial plan's exclusion of remote links; its other security and device-operation requirements remain binding.

## Non-goals

- No Smart App Control policy changes, certificate purchase, signing work, Defender bypass, or Sandbox repair in this change.
- No physical Walkman writes or deletions, publishing, git push, commits, resets, or cleanup of existing dirty work.
- No generic URL extractor, playlists, live streams, authenticated/private content, browser cookies, account login, DRM bypass, streaming-service integration, or background library watching.
- No claim that this device displays album artwork; app artwork support and transferable text metadata are independently testable capabilities.

## Related plans/cards

- Board: external Kanban operations are excluded from this delegated task; no board switch or mutation is authorized here.
- Cards: no external card IDs assigned. The following local work units identify ownership without inventing Kanban IDs.
- Commands already applied / to apply: no Kanban commands. This handoff is already assigned to the active build; the launch commands below are recovery instructions, not a request to start a duplicate build.
- Prior implementation/security context: `docs/archive/IMPLEMENTATION-PROGRESS.md`, `docs/security/NIGHT-OPS-REVIEW-2026-09-05.md`, `packaging/README-NIGHT-OPS.md`.

| Work unit | Owner | Files / responsibility | State at handoff |
|---|---|---|---|
| Integration | Root builder | `backend/application.py`, `backend/media_service.py`, `backend/media_store.py`, boundary integration, dependencies, packaging, integration tests | Assigned |
| Metadata | Metadata worker | `backend/transcode.py`, new focused metadata/artwork module, focused tests | Assigned |
| Link acquisition | Daybreak | `backend/link_import.py`, focused link-import tests | Assigned |
| User interface | Frontend worker | `frontend/` metadata/artwork/link form and UI tests | Assigned |
| Handoff | Handoff packer | This document only | Complete |
| Review | Independent reviewers | Security review, regression hunting, independent verification and report-back | After implementation |

## Constraints

- Recovery profile: `HERMES_HOME=%LOCALAPPDATA%\hermes\profiles\dev` only; `-m Dev --provider moa`, aggregator `grok-4.6`, references `anthropic claude-opus-5` and `openai-codex gpt-5.6-sol`; never `sol-pro`, Nous, Fable, or the default chat profile. Daybreak owns the bounded link-security work assigned above.
- All workers share a dirty workspace. Preserve other edits and coordinate interfaces; never overwrite a neighbor's implementation.
- Secrets stay in environment-backed application machinery. Never copy tokens, ambient downloader configuration, proxy credentials, or browser cookies into downloads, logs, commands, or this document.
- Keep Electron renderer outbound requests restricted to its own authenticated backend origin. Only the guarded backend downloader accesses provider networks; this feature must not loosen CSP, cookies, navigation, IPC sender checks, or backend origin binding.
- Preserve 500 MiB per file, 2 GiB/200-file local import batch, one active import batch, and 10 GiB managed-cache limits. A URL request admits one track and a bounded JSON body. Reserve storage for temporary download bytes as well as final managed artifacts; enforce actual received bytes, not only advertised length.
- Before any metadata/audio/image decoder, analysis, playback, conversion, or device consumer sees media bytes: managed copy/hash, safe format checks, explicit current Defender clearance. Scan generated derivatives before exposure; apply an appropriate scan path to artwork too, without broadening audio-format validation indiscriminately.
- Preserve scan leases, immutable reads, 24-hour/signature/policy-bound clearance reuse, durable per-file results, device admission identity, serialized device access, stale-delete protection, and shutdown draining. Unknown device-write outcomes remain reconciliation-required and must not auto-retry.
- Preserve originals and imported source quality. Playback fallback remains lossless; transfer remains a separate 192 kbps, 44.1 kHz stereo MP3. Enhancement does not alter transfers.

## Tasks (ordered)

### 1. Capture current state and agree interfaces

- [ ] Record `git status --short`, the current commit, and the change baseline without modifying the index. Reuse existing regression/evidence locations and preserve terminal job history.
- [ ] Root and workers agree the URL route and downloader return shape before parallel edits meet. A suitable API is `POST /api/media/import-link` with bounded JSON `{ "url": "https://..." }`, returning the existing job-ID response shape. Keep final route naming consistent across backend, frontend, tests, and documentation.
- [ ] Retain opaque media IDs. The downloader returns only an owned local artifact plus bounded provider provenance/display suggestions; it never returns a renderer playback URL or path supplied by the remote server.

Existing seams verified when this handoff was written:

- `backend/application.py` owns authenticated routes and `import_files`; `backend/security.py` recognizes `/api/media/import` and `/api/upload` for upload-specific limits. Account for the new JSON route explicitly without applying multipart persistence to it.
- `MediaService.run` currently analyzes metadata only for `kind='import'`. `upload`, transfer, restored records, and previously imported unanalyzed records need the same post-clearance metadata preparation when required.
- `MediaService.derivative` invokes `convert_managed(source, out, kind, MAX_FILE)` and writes `transfer.mp3` under an opaque ID directory. JSymphonic must receive explicit usable tags instead of deriving title/artist from those storage names.
- `MediaStore` persists JSON records in SQLite, with `artifacts`, hashes, scan records, queue position, and leases. Add bounded metadata/artwork/provenance fields additively and retain old records.
- `analyze_cleared_audio` currently parses title/artist/album from bounded ffmpeg diagnostics; conversion maps source tags and drops video/art streams. Keep real loudness/duration analysis while introducing structured, bounded metadata/artwork extraction.

### 2. Normalize metadata and repair fallback tags

- [ ] Add fixtures for tagged MP3/FLAC/M4A where supported, Unicode/multiline/control-character tags, missing and blank tags, duplicate filenames, and malformed artwork/tag blocks.
- [ ] Normalize title, artist, album, genre, year/date, and track number with explicit length/type bounds. Valid embedded tags take precedence. Missing title uses the sanitized original filename stem; missing artist/album use stable human-readable unknown values. Never use `transfer`, an opaque ID, or a managed directory as music identity.
- [ ] Keep provider provenance separate from trusted embedded tags. An uploader/channel is not automatically a verified recording artist. Provider title may supply a bounded fallback when embedded title is absent.
- [ ] Pass normalized metadata explicitly into transfer encoding. Preserve valid existing details and use ID3v2.3-compatible text tags. Apply this to both newly imported and already persisted media; invalidate/regenerate a cached transfer derivative when its tag policy/content no longer matches, then rehash and rescan it.
- [ ] Extract supported embedded cover art only after source clearance, under byte/dimension/time limits. Serve only validated, scanned local image bytes through an authenticated same-origin media-ID endpoint, with a fixed image MIME type and no HTML/SVG execution. Do not fetch arbitrary URLs found in tags or render provider thumbnail URLs directly.
- [ ] Make artwork optional: missing/unsupported artwork leaves a useful text presentation. Malformed tags/artwork must not invent malware verdicts or crash the worker. An unverified artifact is never rendered.

### 3. Acquire provider audio through Daybreak's guarded boundary

- [ ] Implement a fixed YouTube/SoundCloud extractor allowlist, explicit supported URL forms, and single-item extraction. Reject credentials in URLs, non-HTTPS schemes, unapproved ports/hosts, playlist/channel/profile inputs, live content, and unsupported providers before acquisition.
- [ ] Check every network request and redirect, including extractor API calls and media CDN URLs. Bind DNS validation to the actual connection and reject loopback, private, link-local, metadata-service, multicast, reserved, and other non-public destinations across IPv4/IPv6; reject DNS rebinding and unapproved CDN hops.
- [ ] Disable generic extractors, downloader auto-updates, ambient configuration, environment proxies, cookie files, external downloaders, user-specified executable paths/arguments, postprocessors, thumbnail downloads, and automatic ffmpeg invocations before clearance.
- [ ] Download bounded audio bytes into a uniquely reserved private temporary path. Enforce total bytes and wall-clock deadline even when headers are missing/wrong or traffic trickles. Fail closed on incompatible formats rather than invoking a decoder before scanning.
- [ ] Root integrates successful acquisition through `MediaStore` and `MediaService`, with durable acquiring/scanning/analyzing/ready/failure states. Temporary files are deleted only when their leases/ownership permit; drain counts the whole acquisition and processing job. Restart marks interruption and never silently restarts a network or device operation.
- [ ] Return actionable structured provider/unavailable/limit/scan failures. Never echo expiring signed media URLs, secrets, private filesystem paths, or unbounded provider output into the UI/logs.

### 4. Wire both interfaces and rebuild product packaging

- [ ] Add the same accessible link-import form to both products, with supported providers, a clear Import action, keyboard submission, pending/busy feedback, and structured failures. Distinguish importing into the queue from transferring selected ready music to a Walkman.
- [ ] Render text metadata as text, show local authenticated artwork or a fallback, and preserve queue identity/order and playback selection across refresh/restart. Imported links remain available in the local queue after transfer.
- [ ] Root pins and packages approved metadata/downloader dependencies and required notices into both isolated Python runtimes. The player still contains no Java/JSymphonic/device modules. Never depend on a developer's globally installed downloader/runtime.
- [ ] Update product documentation and versioned installer artifacts only after source-level review/checks pass. Record exact final hashes and distinguish unsigned-package limitations from this feature's correctness.

## Definition of Done

- [ ] Local tagged music retains normalized text details in the app and in a fresh API → ffmpeg → Java mock-device round trip. Untagged and duplicate-name fixtures never inherit managed filenames/IDs; the original names remain available for explanation.
- [ ] A supported embedded cover renders locally in both products only after clearance; malformed/oversized image fixtures fail safely and old records without art still load.
- [ ] Mocked single-track YouTube and SoundCloud routes produce persistent ready queue entries with provenance, then use the ordinary authenticated range/playback/transfer pipeline. A small live provider probe is supplemental evidence only; mock coverage must remain deterministic and network-independent.
- [ ] Link tests cover redirects, DNS rebinding, private IPv4/IPv6 targets, non-public alternate representations, forbidden provider/CDN hosts, credential URLs, playlists/live inputs, generic extractor fallback, ambient config/cookies/proxies, response-size overflow, missing content length, deadline expiry, and partial-file cleanup.
- [ ] Spy tests prove no decoder or device call occurs before clearance for local and downloaded media, metadata/artwork, and transfer regeneration; unavailable/threat/error/UAC cancellation/changed bytes block their consumers.
- [ ] Import admission and drain tests include acquisition, conversion, concurrent import attempts, removal during active leases, restart interruption, stale scan reuse, and durable errors across missed polling intervals.
- [ ] Actual Electron tests cover link form success/failure, hostile metadata escaping, artwork authentication, queue playback/seek/advance, and close while import is active. Backend-only health is not UI proof.
- [ ] Run from repository root with the configured local/bundled Python: `python -m pytest backend/tests -q`; `npm run test:frontend`; `npm run test:electron`; `npm run test:packaging`; `npm --prefix frontend run build`. Run the repository's existing real ffmpeg/Java mock integration with its documented runtime paths, not substitute mocks advertised as real execution.
- [ ] Build both products with `npm run dist`; independently exercise each final installer using `packaging/smoke-install.ps1` and inspect its result. Any policy-blocked clean-guest or physical-device checks stay explicitly pending; do not claim acceptance from older installers' evidence.
- [ ] Store reproducible command/exit-code logs, focused fixture results, UI captures, mock round-trip details, review findings/fixes, and installer hashes under `packaging/build/media-import-upgrade/`; reference existing test-output locations where the harness writes there.

## Out of scope / Later

- Additional providers, playlists, authenticated libraries, provider thumbnail fetching, device artwork support, and automatic metadata lookup need separate scope and security review.
- Signing, clean Windows/Sandbox policy compatibility, human listening comparison, and physical NW-S705F playback remain separate existing acceptance/release obligations.

## Report back must include

- Files changed, ownership, and exact new API/data contracts.
- Commands and exit codes; distinguish deterministic tests, actual Electron/decoder/Java evidence, live-provider observations, and blocked/manual checks.
- Definition-of-Done checklist with disk proof paths, installer versions/hashes, and remaining compatibility limitations.
- Independent Daybreak-related security review plus bug-hunter and farm-verifier results, findings fixed or unresolved, and blockers for the chat supervisor.
- Mark the local work units complete only with evidence. There are no external Kanban cards to close.

## Launch commands

Use only if resuming this handoff in a fresh approved build session; do not duplicate the active builders.

**PowerShell**

```powershell
$env:HERMES_HOME = "$env:LOCALAPPDATA\hermes\profiles\dev"
hermes chat -q @"
Execute the complete handoff file at C:\Users\rober\Desktop\Projects\walkman\walkman-bridge\docs\plans\2026-09-05-media-import-upgrade.md. Read it before taking action, preserve the dirty workspace and worker ownership, and report independent verification evidence.
"@ -m Dev --provider moa --max-turns 200
```

**cmd**

```bat
"%LOCALAPPDATA%\hermes\bin\dev.bat" chat -q "Execute the complete handoff file at C:\Users\rober\Desktop\Projects\walkman\walkman-bridge\docs\plans\2026-09-05-media-import-upgrade.md. Read it before taking action, preserve the dirty workspace and worker ownership, and report independent verification evidence." -m Dev --provider moa --max-turns 200
```

Suggested next agents: assigned build workers → independent security review and bug-hunter → `@farm-verifier` → `@hermes-reportback`.
