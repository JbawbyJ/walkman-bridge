# Device recovery and remaining assessment fixes

This change closes the interrupted-write retry gate through explicit owner
acknowledgement. It does not infer that an unknown write succeeded and never
retries a transfer automatically.

## Contract

- Persist the captured volume identity with every transfer/delete write intent.
- Inspect the engine journal, track ledger and playlists under the device gate.
- Pending journals, probe warnings, unreadable inventories, other admitted device
  writes, unidentified older jobs and another connected volume prevent acknowledgement.
- Re-read the snapshot on acknowledgement and require its ETag plus an explicit
  `acknowledge: true`. Validate the volume again before committing local evidence.
- Commit the job acknowledgement and media retry flags together in one SQLite
  transaction while still holding the device gate. A final identity failure or
  database failure keeps both retry flags set.
- Preserve the original job status and per-file outcome, including `unknown`.
  Other uncertain jobs referring to the same media retain that media's retry gate.
- Recovery and repair remain separate explicit device-writing actions. Opening
  the panel or inspecting state never invokes them. A failed action discards the
  panel's confirmation state; the owner must inspect again.
- Recovery and repair require `If-Match` from a fresh inspection (428 when
  missing). Both mutations and acknowledgement reject an older engine without
  fingerprint support. Changed journal bytes, Sony database table bytes or volume
  identity invalidate the confirmation even if visible tracks look unchanged.

Legacy jobs without a recorded volume remain blocked. There is deliberately no
blanket "clear all" or automatic retry. Recovery does not substitute for a backup.

## Related defects

- Missing/unknown journal outcomes and incomplete repair summaries now fail closed,
  even when the shim emits `done` and exits zero.
- A current generic import error is no longer replaced by an older playlist fatal.
- Sony playlist slots are lifetime allocations. Editing/reusing an existing
  playlist is supported; deletion does not free its slot. The recovery action is
  now `reuse_playlist`, mapped to the existing playlist update endpoint.
- Queue order accepts the full persistent queue across multiple import batches.
  The 1 MiB authenticated JSON boundary and exact-permutation validation remain.
- Advertised Opus, WMA and AIFF imports preserve their extensions for format
  validation rather than being stored as `.bin`. Defender clearance is unchanged.
- Selected existing Deno notices are tracked with original bytes and provenance;
  product staging requires them. Complete dependency notice reconciliation remains
  a release prerequisite.
- Player staging excludes the device-only reconciliation module.

## Evidence and limits

Regression tests cover missing/stale confirmation, changed volumes, probe warnings,
invalid journals, failed SQL transactions, a disconnect at final validation,
shared-media uncertainty, legacy jobs and UI confirmation/no-retry behavior.
Parser and format regressions failed before their fixes. The 201-item queue was
created through two authenticated imports, then reordered successfully; missing
and duplicated IDs were still rejected.

The runtime lock pins JSymphonic commit
`453c69e5a0146821ddcafd0a0ee9ebbd25322dbb` from
[JSymphonic PR #7](https://github.com/JbawbyJ/jsymphonic/pull/7).
The source archive SHA-256 is
`abb614ba8fe2de7b870710094e16735b25afab903735a6b16fe247ec3da97db5`.
An offline clean package build from the extracted archive produced the test JAR
`556cf46b7453620ae391dd04a983a687df35cf9b5eadd9d5df934d40afdc0997`.
The archive build skipped tests; the engine commit separately completed 132 tests
with zero failures/errors and four optional Sony-fixture skips. Bridge's real
Java/FFmpeg integration selection passed 20 tests on disposable libraries; the
recovery API case passed again after mandatory confirmation was enabled.

Staging invokes the candidate JAR against a new empty temporary mount and rejects
missing recovery fingerprints, invalid recovery completion or successful repair
of an unloaded library. These checks remain active under optimized Python.
Current frozen-artifact verification pins the independently checked local JAR
above and validates the exact source ZIP, safe unique archive paths and license
parity. It no longer assumes a sibling engine checkout. The historical 0.4.0
verifier retains its original binary pin. A future rebuilt JAR needs its own
binary review evidence; this does not claim reproducible JAR timestamps.
Fingerprints detect changed snapshots; they do not authenticate an attacker who
can rewrite an entire device or make arbitrary external writers atomic.

The full backend/packaging run completed with 461 passes, 15 skips, seven expected
failures and one existing unexpected pass. The final source-verifier selection
passed 23 additional tests; the complete frontend selection passed 75 tests and
the final recovery UI selection and production build passed. Independent manual
AppSec checks covered 33 negative/rollback cases. Dedicated hosted review tools
were unavailable; these were scoped manual and executable checks, not a claim of
completed hosted review or full penetration testing.

All device work in this task targets disposable fixtures. No physical Walkman,
real Defender scan, alternate-account UAC prompt, signing or release publication
is part of this evidence. Hardware power-loss behavior and firmware acceptance
remain separate. Source inspection, automated tests and staged artifacts are not
physical acceptance.
