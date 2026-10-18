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

All device work in this task targets disposable fixtures. No physical Walkman,
real Defender scan, alternate-account UAC prompt, signing or release publication
is part of this evidence. Hardware power-loss behavior and firmware acceptance
remain separate. Source inspection, automated tests and staged artifacts are not
physical acceptance.
