# Management 0.4 backend independent review

Reviewed 2026-09-05 local time / 2026-09-06 UTC by the resize/UI worker, who did not author the backend implementation reviewed here. This pass changed only this report. No physical Sony path, installation, packaging build, Java build, or production backend was used.

## Verdict and findings

**No material defect was confirmed in the scoped Python management changes.** The backend authorization, stale-write, lease, persistence, and metadata-revision contracts passed the independently executed targeted checks below. This is a scoped backend review, not approval of physical device behavior or the Java table writer, which remain separate verification responsibilities.

Specification result: **Pass for the exercised local management and Python native-route contracts.** Engineering result: **Pass for the reviewed backend changes, with the execution limits below.** There are no open actionable backend findings from this pass.

## Scope and source identity

Compared the new management seams against `../implementation-evidence/management-resize-baseline-20260905-194625.zip`. The archive contains the prior four backend modules; `backend/playlists.py` is new. Read the route callers, store lifecycle, operation coordinator, boundary middleware, metadata normalizer, and relevant tests to trace the new behavior. Java source, UI behavior, and previous reviewers' Java/UI findings are outside this verdict.

| File | SHA-256 reviewed |
| --- | --- |
| `backend/media_store.py` | `52BD6F0155DCCF8B76A8E310D2012FF3D49CEE616A2903DD6F212285681088F7` |
| `backend/playlists.py` | `387C7839B7F8E0C0DCE6E1DBE6635C52F9A9381ECBF43FD92B170BDA2E43F16E` |
| `backend/application.py` | `2CDC9D63E658894748318750F68BB78299CBD2135F08E07B4A0D86D726D27AD2` |
| `backend/jsymphonic.py` | `1EAB883FDBF48DD6203FF758C11699B44CFE5D3A3336AFF7AF29F899B652ECD9` |
| `backend/media_service.py` | `CB714B2FEFE0A9A9D0681EEF14C3BA12F403D418627325C0BAEB3939EAE49950` |

## Risk-to-evidence matrix

| Risk | Reviewed behavior and independently executed evidence | Result |
| --- | --- | --- |
| Unauthorized management requests | `application.py:157` installs the same exact-host/origin/session boundary before local/native route registration. New body models forbid extra fields. `test_playlist_edits_obey_authentication_and_drain` rejects hostile-origin local, metadata and Sony writes; existing API tests reject invalid host/session and cookie writes without the correct Origin. | Pass |
| Product separation | `application.py:422` registers local management for both products; `application.py:423` registers native management only inside the Bridge branch. Existing Player API tests independently prove device/transfer routes are absent; the new native-playlist exclusion was inspected in the registration path. | Pass, with native-playlist exclusion established by source inspection rather than a dedicated request assertion |
| Local stale updates and persistence | `media_store.py:384` performs ETag comparison and update under the same reentrant store lock. Changes advance revision; stale update/delete returns 409. Reopen tests verify stored names/order, and deletion retains media. `test_playlist_membership_rejects_stale_and_unknown_and_library_removal_prunes` covers unknown/deleted IDs and stale revisions. | Pass |
| Membership ghosts or premature cache deletion | `media_store.py:293` atomically tombstones library membership and removes references from every saved playlist, incrementing affected revisions. Cleanup remains lease-gated. Tests hold real store/playback leases, remove a library row, prove bytes survive until release, and prove saved lists no longer reference removed IDs. | Pass |
| Invalid saved playback scope | `media_store.py:448` validates non-null playlist IDs under the store lock; playlist deletion clears the saved playlist ID in the same SQLite transaction while preserving track/position. The saved-scope restore/delete test verifies this directly. Renderer handling of a changed membership is outside this pass. | Pass |
| Metadata edits racing processing | `media_store.py:427` rejects edits while `_working` contains the ID, under the same lock used by `work()`. `MediaService.hold()` acquires exclusive processing admission before background execution. Editing while holding only a playback lease remains allowed because it changes SQLite text, not audio. The metadata lease/work test proves both cases and unchanged source bytes. | Pass |
| Stale tagged transfer artifacts | `media_store.py:445` increments metadata revision and preserves explicit overrides. `media_service.py:146` invalidates a transfer derivative only on the next admitted conversion when its revision/policy differs. The regeneration test proves an existing derivative survives the edit, then is regenerated using updated tags; forced reanalysis retains overrides. | Pass |
| Concurrent Sony edits or volume replacement | `playlists.py:161` obtains the device session, rereads both ledger and playlist snapshot, and checks the caller's ETag inside the serialized device gate. The deterministic two-thread test yields exactly one 201 and one 409 with one writer call. Volume-swap tests reject before writing, and post-write disconnect is classified unknown. | Pass at injected Python device boundary |
| Lost native write history / automatic retry | `playlists.py:174` persists `device_writing` with the transferring row before calling the engine; error handling records unknown outcome and reconciliation requirement. The restart test reopens real SQLite and verifies unknown state persists with exactly one attempted writer call. Admission tickets are released after terminal paths and new writes are refused during drain. | Pass at injected Python device boundary |
| Unsafe native arguments or false terminal success | `jsymphonic.py:335` transports UTF-8 names as ASCII base64 with argument arrays, never a shell command; IDs are restricted decimal strings. Parser tests require the final terminal marker, reject fatal/incomplete/malformed results and wrong counts, and preserve repeated existing Sony members. Names exceeding the API's native UTF-16 budget are rejected before admission/writes. | Pass for Python adapter; real Java argv/bytes remain separate |
| Metadata normalization and codec compatibility | Tests reject malformed year/track fields and unsupported metadata properties. The independently enabled real ffmpeg tests verify tagged/untagged conversion, Unicode metadata, artwork bounds, and multiline metadata that must not forge another tag/duration. All inputs are generated in temporary directories. | Pass |

## Fresh executed commands

Working directory for every command: `C:\Users\rober\Desktop\Projects\walkman\walkman-bridge`.

1. Targeted management and adjacent regression suite:

```powershell
./backend/.venv/Scripts/python.exe -B -m pytest backend/tests/test_playlists.py backend/tests/test_native_playlist_api.py backend/tests/test_media_store.py backend/tests/test_metadata.py backend/tests/test_playback_lease.py backend/tests/test_api.py -q -p no:cacheprovider
```

The initial restricted launch exited 1 before Python tests started: the venv launcher could not create its existing UV-managed base interpreter process. The exact same command was then independently executed with sandbox escalation and exited **0**: **51 passed, 4 skipped, 1 warning in 8.61s**. The warning is Starlette's existing `httpx` TestClient deprecation notice. The skips were three explicitly opt-in real ffmpeg tests and the explicitly opt-in real native playlist backup-copy test.

2. Real generated-audio metadata/artwork checks, with only the codec test opt-in enabled:

```powershell
$env:NIGHTOPS_REAL_MOCK = '1'
$env:WALKMAN_BRIDGE_FFMPEG = (Resolve-Path '../Walkman Bridge/ffmpeg/ffmpeg.exe').Path
./backend/.venv/Scripts/python.exe -B -m pytest backend/tests/test_metadata.py -q -p no:cacheprovider
```

Executed with sandbox escalation; exit **0**, **11 passed in 0.99s**. This includes the three real ffmpeg tests skipped in command 1. Counts from the two commands overlap and should not be added as unique test totals.

3. Source identity and baseline inventory were read using `Get-FileHash -Algorithm SHA256` and .NET `System.IO.Compression.ZipFile.OpenRead`; no archive was extracted into the checkout and no source file was rewritten.

## Limits

- `NIGHTOPS_SONY_FIXTURE` was not enabled. The native API test therefore did not copy or invoke the real engine against a saved Sony backup during this pass. Parent/engine verification owns that requirement.
- No physical Sony read/write, firmware playback, installer execution, or distribution build occurred.
- Native concurrency, malformed responses, volume changes, and uncertainty were exercised with injected device/shim boundaries and real SQLite; this does not prove the Java transaction's on-disk guarantees.
- Local single-store synchronization was traced through the reentrant lock and SQLite transactions. This pass did not stress multiple independent application processes against one data directory, which the product's single-instance ownership is intended to exclude.
- No dependencies, production code, tests, runtime configuration, or user data were changed by this review.
