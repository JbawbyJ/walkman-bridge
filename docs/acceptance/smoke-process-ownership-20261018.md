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
