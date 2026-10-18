# Renderer stop deadline

Base: PR12 `66d0c164ba3f9bd016aee84db7d44ed3e8e5fcbd`.

The ten-second playback-stop deadline and renderer-death listeners now start
before `executeJavaScript`, covering a renderer that cannot execute the stop
script. The bounded readiness wait remains unchanged. A timeout uses the existing
forced-close path: destroy the renderer, release playback, drain admitted work,
then terminate the backend only after idle is confirmed. Late script completion
cannot send another stop request, and all stop listeners are removed on settlement.

Both new regressions failed on the PR head: a pending script never reached the
deadline, and confirmed renderer loss still waited for the script. They pass with
the fix, including destruction before lease release, deferred backend termination,
and rejection of late stop IPC.

Fresh validation on 2026-10-18:

- `node --test electron/boundary.test.cjs electron/lifecycle.test.cjs electron/playback-stop.test.cjs electron/scanner-broker.test.cjs`:
  40 passed, 0 failed.
- `git diff --check`: passed.

These tests execute production main/preload code with Electron, process, and
network boundaries mocked. The real Electron close harness and full
`test:electron` command were not run under the task's no-GUI restriction.
Independent review and native runtime verification remain separate gates.
