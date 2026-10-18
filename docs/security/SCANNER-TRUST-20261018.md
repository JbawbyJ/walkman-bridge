# Scanner trust follow-up

Reviewed base: PR13 `be4e72e7cde913d37c79693c0215b10dda4ba1f4`, compared with
main `6ffb9c9`. Its scanner broker change makes managed-path classification use
Windows path rules. The following issues predate that patch.

## Findings and scope

- `backend/scan.py` selected `MpCmdRun.exe` from inherited `ProgramFiles`,
  `ProgramData`, then `PATH`. `electron/main.cjs` forwards the launch environment.
  A local launcher controlling those values could select an arbitrary executable
  and have a zero exit treated as a scan result. This is a local launch-environment
  trust problem, not evidence of a remote attack or privilege escalation.
- Registry status fallback also inferred the Defender platform version through
  inherited `ProgramData`.
- The native helper used Windows-known roots already, but did not reject reparse
  paths or retain the selected executable's identity before launch.
- During alternate-admin UAC, the server ACL only granted the elevated account
  and Administrators. The original standard-user exchange process could therefore
  fail to connect despite being the expected PID.

## Changes

Ordinary discovery and registry platform status now share Windows-known roots,
numeric platform directories and reparse rejection. Missing discovery fails
closed with no environment/PATH fallback. Before ordinary or elevated launch,
the selected file must be an exact allowed candidate; a read-only Windows handle
checks its final path and denies write/delete sharing for the scan duration.
Scanner child working directories and relevant environment roots are fixed from
Windows APIs; runtime and module injection variables are removed.

The pipe ACL adds only the expected client token's SID, obtained from Windows.
The helper retains the expected process handle across the exchange to prevent
PID reuse, and still checks the actual connected PID before reading requests.
The existing reciprocal server PID check remains in place.

No production bypass, scanner override, broad user ACL, or real-scan test switch
was introduced. Existing native one-shot tests now send a mismatched content hash
so they exercise the pipe/protocol path without invoking Defender.

## Trust and acceptance limits

Windows-known installation roots and their OS-managed ACLs remain trusted. A
separate Authenticode publisher policy was not added; compromised administrators
and tampered OS installations are outside this local environment-spoofing fix.
Windows folder discovery, read-handle identity, write/delete denial, a real local
junction, token-SID lookup, and same-user pipe PID verification are exercised
without elevation. A synthetic alternate-account SID tests the ACL contents;
real cross-account UAC and real Defender scanning remain unverified here.

No GUI, physical Walkman, device discovery, or live scan is part of this change's
test execution. Full backend tests use an isolated mock-device path.

## Verification on 2026-10-18

Python 3.11.15 / pytest 8.3.3 were run from the existing backend virtualenv,
with this isolated checkout as the working directory. Native builds used the
existing .NET 10.0.400 SDK and NuGet cache, with build outputs and CLI home in
this checkout's ignored `scanner-helper/artifacts` tree.

- Before the Python fix, all three spoofed environment cases selected their
  fake executable; the initial new-test run had nine failures. No executable
  fixture was launched. The first test attempt failed during temporary-directory
  setup; a checkout-local `--basetemp` resolved that environment issue.
- `python -m pytest backend/tests -q -p no:cacheprovider --tb=short
  --basetemp=.ci/scanner-full-test-1`: 328 passed, 14 skipped, 7 xfailed,
  1 xpassed; existing Starlette/httpx deprecation warning. `MOCK_DEVICE_PATH`
  and `WALKMAN_JOBS_DB` pointed into this checkout's `.ci` directory.
- After adding the final invocation-lock regression, focused
  `test_scanner_trust.py test_scan.py test_scan_bridge.py test_defender_status.py`:
  79 passed, 1 skipped (pre-existing symlink test lacks Windows privilege).
- `python -m pytest backend/tests -q -m xfail -rxX`: 7 xfailed, 1 xpassed.
  The unexpected pass is
  `test_checksum_mismatch_during_device_write_is_not_success`. The seven existing
  expected failures concern closed/stale job stores, unconfirmed device results,
  short downloads, mutable track caches, NUL filenames and empty streams; these
  are outside this scanner branch.
- `node --test electron/*.test.cjs`: 25 passed.
- `dotnet build ScannerHelper.Tests/ScannerHelper.Tests.csproj -c Release
  --artifacts-path artifacts/build --no-restore`: zero warnings/errors;
  `ScannerHelper.Tests.exe`: 12 passed. The newly introduced native API tests
  first failed compilation before those APIs were implemented; their Windows
  ACL/token/pipe behavior then passed. No real cross-account UAC was exercised.
- `dotnet publish ScannerHelper/ScannerHelper.csproj -c Release
  --artifacts-path artifacts/build -o artifacts/win-x64 --no-restore`: passed.
- `tests/Verify-PublishedHardening.ps1`: `startup_hook_executed=false`, invalid
  request exit 1. Published helper SHA-256:
  `9F59F00E7C24B0FA0D2030608C2D080FB7ABA46000B0062E1E240D3AEDB6BE1F`.
- `git diff --check`: passed.

The initial NuGet restore could not access the network; restore succeeded using
the pre-existing cache with failed sources ignored and NuGet audit disabled for
this offline build. This is build/test evidence, not a fresh dependency audit.
