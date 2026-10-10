# Independent manual review: media import upgrade

Review date: 2026-09-05. Target: the current media-upgrade changes relative to
`../implementation-evidence/media-upgrade-baseline-20260905-230619.zip`.
Baseline SHA-256: `e90ef9aa8ca4cc939640f1ce5e551e4c5b3f2a0249babed9fd81aaba48b3ab2a`.

**Final scoped verdict: pass after repairs.** No unresolved material finding
remains in the reviewed media-upgrade source at the hashes recorded below.
Product release acceptance is still partial: this review does not certify
signing, a clean Windows guest, physical hardware, or publication readiness.

This is an independently performed manual security and correctness review. The
specialized `security-review` and `bugbot` subagent types were unavailable; this
report does not represent a successful run of either specialized service. The
reviewer changed this report only, and used the code-verification workflow for
source inspection and deterministic negative probes. Builders were integrating
fixes concurrently, so findings below identify both the observed defect and the
status of its follow-up verification.

## Findings discovered

### P1: yt-dlp could invoke an audio postprocessor before Defender clearance

- Original location: `backend/link_import.py`, downloader options and
  `SecureYoutubeDL`; relevant pinned dependency:
  `yt_dlp/YoutubeDL.py:3608-3666`.
- `postprocessors=[]` did not disable yt-dlp's automatic fixups. With an M4A
  result marked `container=m4a_dash`, the real pinned `YoutubeDL.process_info`
  adds `FFmpegFixupM4aPP` if FFmpeg is available. This happens before
  `MediaService.run_link_import` receives the file for managed copy and scanning.
- Independent reproduction used `YoutubeDL.process_info`, an overridden
  downloader writing harmless synthetic bytes, and a fake available M4A fixer.
  Observed output: `{'implicit_postprocessors': ['FakeFixup']}`. No network or
  real audio decoder was used by this probe.
- Remediation: disable fixups explicitly, reject decoder postprocessors, and
  retain a download path that cannot select an external decoder/merger.
- Status: **fixed and independently verified**. The real pinned downloader's
  `process_info` was exercised with the repaired secure subclass and an M4A
  DASH fixture. A trap fixer was never constructed; the explicit refusal and
  fixed-output postprocessing bypass also passed the focused suite.

### P1: acquisition could exceed its advertised wall-clock deadline and hold shutdown open

- Original location: `backend/link_import.py`, `_BoundedReader.read`,
  `_open_https`, `_resolve_public`, and the Deno runtime invocation.
- A socket timeout limits one blocking receive; it does not limit the entire
  `HTTPResponse.read(n)` or header parser while a peer keeps sending small
  amounts of data. DNS resolution also lacked a hard parent deadline. The
  pinned Deno provider called `communicate_or_kill(stdin)` without a timeout.
  The admitted import ticket remains live until acquisition returns, so an
  indefinitely stalled acquisition also prevents drain completion.
- Independent reproduction used a local socket pair, a 0.120-second deadline,
  and 20 harmless bytes sent every 0.04 seconds. The call returned a timeout
  only after **0.813 seconds**. Extending the trickle extends the blocking read;
  no external server was contacted.
- Remediation: isolate acquisition in a child process with a hard parent
  deadline, descendant containment, bounded output, and termination/reaping
  before temporary-file cleanup. Keep device operations outside that process
  tree.
- Status: **fixed and independently verified on Windows**. A real owned worker
  and its sleeping descendant were contained by the production Job Object
  boundary. A 0.400-second parent deadline returned in 0.422 seconds, with the
  worker reaped, the descendant exited, and both pipes closed. Device operations
  are outside this acquisition process tree.
- Follow-up review caught incomplete cleanup for callback/thread-start errors
  and an unknown-length progress stream that could exceed its protocol budget.
  The final worker catches every escaping exception across startup and operation,
  waits/reaps before releasing its caller, coalesces unknown-length progress in
  5 MiB buckets, and caps progress at 101 messages. Independent real-worker
  callback and reader-start failure probes both confirmed reaping and closed
  pipes; the full-size unknown-length progress regression passed.

### P2: raw provider errors reached persistent application logs

- Original locations: `backend/link_import.py` downloader options;
  `electron/main.cjs:109`; pinned `yt_dlp/YoutubeDL.py` `report_error`, `trouble`,
  and `to_stderr`.
- `quiet=True` and `no_warnings=True` do not suppress errors. yt-dlp emits the
  raw error before raising `DownloadError`; sanitizing the caught exception
  only protects the API response. Electron persists backend stderr in
  `nightops.log`, so signed provider URLs or other provider diagnostics could
  reach that log.
- Independent reproduction called the real library's `report_error` with a
  synthetic signed-URL sentinel under the production quiet/warning options.
  The captured stderr contained the sentinel. No real credential was used.
- Remediation: discard acquisition-worker stderr and emit only bounded,
  structured, sanitized messages. Do not forward raw child output as progress
  or error details.
- Status: **fixed and independently verified**. The process boundary discards
  stderr; only bounded JSONL results/progress cross back to the backend. The
  real-worker deadline probe emitted a synthetic provider stderr sentinel that
  did not reach parent output. Malformed protocol and error cleanup are covered
  by the focused checks and source inspection.

### P2: provider year/track fields were discarded and uploader was promoted to artist

- Original locations: `backend/link_import.py` returned metadata dictionary;
  `backend/media_service.py` `run_link_import`; `backend/metadata.py:42`.
- The downloader returned `release_year`, which normalization did not
  recognize, and `track` held the song title instead of `track_number`.
  Normalization therefore discarded available year and numeric track details.
  The fallback artist also used an uploader/channel identity despite the
  design's distinction between provider provenance and recording artist.
- Independent normalization probe with year 2024 and track number 7 returned
  only title, artist, and the album fallback.
- Remediation: map provider fields to the normalized year/numeric-track
  contract; keep uploader provenance separate from artist.
- Status: **fixed and independently verified**. The downloader now maps `year`
  and numeric `track_number`, retains uploader separately, and does not use it
  as artist fallback. Focused contract tests and real API/ffmpeg/Java metadata
  round trips passed.

### P2: interrupted downloads retained a live per-file state after restart

- Original location: `backend/jobs.py`, `JobStore.recover_interrupted`.
- The new `downloading` file state was absent from the interruption transition.
  Restart changed the job to `interrupted` and removed its temporary download
  directory, but its file row still said `downloading`.
- Remediation: include new admitted acquisition/analysis states in restart
  recovery and verify no automatic replay or device uncertainty is introduced.
- Status: **fixed and independently verified**. `downloading` and `analyzing`
  join the interruption transition. The restart regression verifies matching
  interrupted job/file states, removal of partial download files, and no
  device-reconciliation flag for a download that never reached device work.

### P2: rejected raw tag values could remain in persisted metadata

- Original location: `backend/media_service.py:190`, `prepare_metadata`.
- Persisting `{**measured, **tags}` retained raw date/year/track/genre values
  when normalization omitted an invalid value. Successful title/artist/album
  normalization did not remove those other rejected raw fields.
- Remediation: persist normalized tags and an explicit allowlist of measured
  audio facts, rather than the raw tag dictionary.
- Status: **fixed and independently verified**. Persistence now writes explicit
  normalized optional tag values plus an allowlist of measured audio facts.
  The final focused regression verifies removal of rejected raw tag values.
  Source-clearance failures during optional artwork preparation also propagate
  to the import outcome rather than being swallowed as an optional image error.

## Scope and observations

Reviewed the changed acquisition, metadata, artwork, persistence, API, boundary,
and packaging paths: `backend/link_import.py`, `metadata.py`, `transcode.py`,
`scan.py`, `media_service.py`, `media_store.py`, `application.py`, `security.py`,
the relevant job/operation callers, frontend link dialog and artwork rendering,
dependency locks, and Deno staging. Followed relevant behavior into the installed
checksum-pinned yt-dlp implementation. Newly added worker containment was
included in the follow-up review.

Static inspection found these useful protections intact:

- Provider inputs use an explicit single-track HTTPS allowlist; outbound
  requests and redirects revalidate approved hosts, reject non-public DNS
  answers, and connect to a selected validated address with original-host TLS
  verification. The renderer does not gain external network access.
- Selected and downloaded audio converge on managed files and current Defender
  clearance before application metadata analysis, conversion, or playback.
  Artwork is generated under a cleared source read lease, bounded to JPEG, and
  receives its own clearance before the authenticated endpoint exposes it.
- Media IDs and output names stay separate from provider display text. Cache
  path components reject links/reparse points; source and image read leases
  prevent concurrent Windows writes/deletion during scanning and consumption.
- React renders metadata as text. Cover URLs must exactly match the media ID's
  same-origin artwork route. API origin, cookie/token, CSP, and internal-route
  restrictions remain in effect.
- Temporary downloads count against cache reservation and are removed on
  normal failure/restart. Admitted acquisition participates in the existing
  operation coordinator; it does not perform device writes directly.

These are source-level conclusions, not evidence of a fully tested security
boundary or a clean-machine release pass.

## Direct execution evidence

The reviewer independently executed three deterministic Python probes using
`backend/.venv/Scripts/python.exe`: the socket-pair deadline probe, the real
yt-dlp implicit-fixup/logging probe, and the metadata normalization probe. Each
completed with exit code 0 and the observed results described above. The
interpreter launcher could not run in the default sandbox, so the same scoped
commands were approved for execution outside that launcher restriction.

No external downloads, malware, real-device operations, policy changes, commits,
publishing, or production edits were performed by this reviewer. Builder and
other verifier test claims are not counted as this reviewer's independent
execution evidence.

## Final follow-up verification

The reviewer independently reran these checks after repairs:

| Check | Fresh result | What it establishes |
|---|---|---|
| `python -m pytest backend/tests/test_link_import.py backend/tests/test_media_upgrade.py backend/tests/test_metadata.py backend/tests/test_scan.py -q` | 74 passed, 3 skipped, exit 0; 3.86 seconds | URL/transport gates, fixed Deno permissions, bounded protocol/progress, metadata normalization, scan/artwork gates, restart/drain, and callback cleanup. The three real-ffmpeg tests are opt-in and were executed separately below. |
| `NIGHTOPS_REAL_MOCK=1`, explicit staged FFmpeg and JDK paths; `python -m pytest backend/tests/test_metadata.py backend/tests/test_metadata_device_roundtrip.py -q` | 16 passed, exit 0; 6.45 seconds | Real ffmpeg tag fidelity/artwork processing and fresh API-to-Java mock-device outcomes. Scanner injection is explicit; no physical Walkman is accessed. |
| Real Windows contained worker plus descendant, 0.400-second deadline | Returned in 0.422 seconds; worker reaped, descendant exited, pipes closed | A stalled acquisition cannot hold the import ticket indefinitely, and closing the Job kills its descendants. |
| Real worker, injected progress callback exception | Returned in 0.375 seconds; worker reaped, pipes closed | Persistence/callback errors do not abandon a live download worker. |
| Real worker, injected reader-thread startup exception | Worker reaped, pipes closed | Failure before sending worker input is cleaned up. |
| Independent network-guard matrix using injected DNS/transport | 7 private IPv4/IPv6 cases rejected; unapproved redirect stopped before another connection | Includes loopback, private, link-local/metadata-service, and IPv4-mapped IPv6 targets. No external traffic was sent. |
| Real pinned `YoutubeDL.process_info` through repaired secure subclass | M4A fixer trap was not constructed | The original pre-scan implicit-fixup trigger is blocked. |

The Python suites emitted one existing Starlette/httpx test-client deprecation
warning. It did not change test outcomes. The real Deno permission test denies
filesystem, network, and subprocess operations; it uses the fixed local runtime.

Final reviewed SHA-256 values:

| Source | SHA-256 |
|---|---|
| `backend/link_import.py` | `b79286392f80d345193ccb4618e2aaa75d50513c76194ad7dcdceed761f1354c` |
| `backend/link_download_worker.py` | `f0e9beb774e558034b1957ffcece2c30f53c7ecdb985b9aad986a62088fcc46d` |
| `backend/media_service.py` | `44ce6f2882cb814d21b1bed146625f7fb22a0893dfefa5600ad75fc78b6ed2bc` |
| `backend/metadata.py` | `814124265ed576ca89acc76baa46e7dfb601b863954fb86bdc9475780bef3829` |
| `backend/transcode.py` | `445450e47626b7765aa53011a26595b09773d406c33a6df4202b9c212583c860` |
| `backend/scan.py` | `69b12149a7282c66195f90bc1eac05a8bd5a4f3d0692b9e3705534e69a278b29` |
| `backend/jobs.py` | `68fde2c39591d5ae491e92e9a677fed9e3b4d6ec3bb4cb099ebf74be23009b00` |

The specification and engineering verdicts pass for this bounded source review
and its explicit evidence. No live provider or real Defender run was performed
by this reviewer, and no installer artifact was certified here. Other agents'
live/API/Electron/installer results remain separately attributed. Linux process
group behavior was read but not executed on this Windows host. Clean guest
compatibility, code signing, provider availability over time, full dependency
notice reconciliation, human listening, and physical NW-S705F playback remain
separate acceptance/release obligations.
