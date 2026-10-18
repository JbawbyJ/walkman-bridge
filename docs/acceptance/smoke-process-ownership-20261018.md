# Installed smoke process ownership

Base: PR10 `cd156390114a7ac18a6867cb4cebbc5386263f00`.

Cleanup now authenticates the smoke root against its recorded launch time before
traversing descendants and again before terminating that PID. It allows the
existing one-millisecond Process/CIM rounding difference. Missing launch identity
fails discovery; missing process timestamps cannot authorize termination. A helper
under the unique smoke-run directory remains discoverable after the root exits.

The new regression first failed by admitting replacement root 32100 and its
unrelated child 32101. After the fix, only independently owned helper 32102 is
eligible. It also covers the termination guard, valid descendants, timestamp
rounding, unknown root timestamps, and missing launch identity. Process discovery
and termination APIs are mocked throughout; no real process is killed.

Fresh validation on 2026-10-18:

- `node --test packaging/products.test.cjs packaging/smoke-ownership.test.cjs`:
  4 passed, 0 failed.
- `git diff --check`: passed.

PowerShell 7 runs the regression, matching CI's `pwsh` shell. Windows PowerShell
5.1 refused script-file execution under this host's existing policy; no policy
was changed. Installer/hang-process and Windows Sandbox tests were not run under
the task's no-GUI/no-real-process-termination restriction. These checks do not
replace independent review or installed-product verification.

## Follow-up: pin identity through termination

Independent review reproduced a remaining check-to-use race: a child PID could
be reused after the CIM timestamp check but before `Stop-Process -Id`. Cleanup
now opens one native process handle, reads its creation time, and calls
`TerminateProcess` through that same handle. It never reopens the numeric PID to
terminate it. The handle is disposed after validation, termination, or failure.
CIM's microsecond timestamp precision permits only the native FILETIME's
sub-microsecond remainder when comparing the recorded child identity.

The new mocked race regression failed on `27cea280` by selecting the replacement
process. It passes with the handle-based implementation. Additional checks cover
a replacement present before open, timestamp precision, and failures while
opening, querying, or terminating. The Windows native adapter check compiles the
C# adapter, reads this test process's identity, and disposes its handle without
calling termination. A separate read-only self-process CIM/native comparison
also matched on this host; its initial sandboxed CIM read was denied, and the
approved read-only retry succeeded.

Fresh follow-up validation:

- `npm run test:packaging`: 12 passed, 0 failed, 0 skipped.
- `git diff --check`: passed (working-copy CRLF normalization warnings only).

The existing packaging suite includes termination of its own timed-out child
fixture. The new cleanup regressions mock termination; no user process or
physical Walkman was touched. Real installer cleanup and Windows PowerShell 5.1
runtime behavior remain unverified. This follow-up requires independent review
because its author performed the earlier review.

## Follow-up: children surviving the original parent

The existing Windows hang test reproduced a regression at `0e7c1a15`: after
the parent exited, its external PowerShell child was treated as a clear tree
and remained running. The new mock initially failed with expected child IDs
`32101,32102` and an empty discovery result.

Discovery now reads the retained launch Process object's verified creation and
exit times after taking the CIM snapshot. When the original root is absent,
a direct child's creation time must fall within that original lifetime.
Further descendant edges require live snapshot parents and creation ordering;
missing intermediate parents do not imply ownership. A visibly reused or
unverifiable root is still rejected, as are children born before launch or
after the original exit. Missing exit identity cannot report a clear tree.
The final creation-time check and termination still use the same native handle.
The contained-executable fallback and command-line-spoof rejection are unchanged.

The fixture retains its launch handles, checks that the lingering child was
born within its parent's lifetime, and uses identity-bound cleanup. It starts
only disposable hidden PowerShell sleepers; no installer, device, or UAC flow
is exercised. Its cleanup directories are checked against their expected
parents before recursive deletion.

Fresh validation on 2026-10-18:

- `node --test packaging/smoke-ownership.test.cjs`: 2 passed, 0 failed.
- `npm run test:packaging`: 12 passed, 0 failed, 0 skipped.
- `pwsh -NoProfile -File packaging/smoke-install.hangtest.ps1`: passed redaction,
  live-parent tree cleanup, and exited-parent lingering-child cleanup; the
  unrelated sleeper survived both cases. The initial sandboxed attempt could
  not query CIM; the authorized retry used only these disposable fixtures.
