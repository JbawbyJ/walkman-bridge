# Sandbox 0.4.1 acceptance harness

Date: 2026-09-06. Scope: the host/guest Sandbox harness and focused harness tests. This work does not change application code, Windows security policy, Hyper-V VMs, or the physical Sony device.

## Artifact selection and execution

Run the host script from the repository with a script host permitted by its existing policy:

```powershell
pwsh -NoProfile -File packaging/windows-sandbox-smoke.ps1 -Version 0.4.1 -BridgeSha256 00027142D787EA7BB99D4E2E0E2614A2A5A0C77464FDC2CE89D576CF5EFB5C83 -PlayerSha256 3A35F8D5E6AEDCC1C24382935123F15A5736D5B8A52F72D3B9828A60001A1047
```

Add `-PrepareOnly` to produce the complete manifest, staged artifacts, configuration, and host evidence without starting Sandbox. The version and both expected SHA256 values are mandatory. The host checks the version against `package.json`, derives exact installer/executable names from `packaging/products.cjs`, verifies both source hashes, and verifies copied hashes. There is no newest-installer selection. The generated schema-2 manifest also binds the guest script and product configuration hashes.

| Product | Exact installer | SHA256 |
| --- | --- | --- |
| Bridge | Walkman-Bridge-Setup-0.4.1-x64.exe | 00027142d787ea7bb99d4e2e0e2614a2a5a0c77464fdc2ce89d576cf5efb5c83 |
| Player | Red-Lotus-Player-Setup-0.4.1-x64.exe | 3a35f8d5e6aedcc1c24382935123f15a5736d5b8a52f72d3b9828a60001a1047 |

The Sandbox receives only `C:\RedLotusInput` (read-only) and `C:\RedLotusResults` (writable). Networking, clipboard, audio/video input, printer redirection, and vGPU are disabled. Installers are copied into guest-local `LocalAppData\RedLotusSandboxAcceptance\<product>\installer`, rehashed, and launched there. Bridge's expected executable is `Walkman Bridge.exe`; both products use `RedLotus.ScanHelper.exe` and `redlotus.log`.

The logon bootstrap records its policy and starts an ordinary Windows PowerShell `-File` child. It does not override execution policy, re-evaluate the script to avoid policy enforcement, unblock files, request elevation, or alter services/Defender/AppLocker/Code Integrity. If that normal script launch is blocked, the bootstrap records the failure and collects read-only token, Defender-service, App Control, and event diagnostics. A missing `guest-result.json` never counts as an installation pass.

## Evidence and failure distinctions

The guest records the current token's elevated flag and elevation type, administrator membership, `whoami /all`, PowerShell language/execution policies, CiTool policy listing, service state, Defender status/version/signature, and Authenticode status for each installer/application/runtime. A stopped WinDefend service or missing MpCmdRun is `defender_unavailable`; a denied CIM/status query is `permission_denied`. Other failed scan attempts are `defender_probe_failed` rather than a claim of absence. A real harmless local text-file scan is attempted only when the Defender command exists and the service is not known stopped. No service is started or policy changed.

Each product records timestamped `mapped_hash`, `guest_local_copy`, `install_launch`, `install_wait`, runtime launch/wait, `smoke_launch`, `smoke_wait`, `uninstall_launch`, and `uninstall_wait` phases. Failures retain their original phase, exception chain, native code, and classification. Code Integrity/AppLocker/Defender events retain original XML and record IDs/timestamps; event-query denial is recorded, not interpreted as an empty log. Process execution uses a retained handle with asynchronous output draining; null exit codes cannot pass.

`application_control_block`, `elevation_required`, `permission_denied`, `timeout`, and `application_or_harness_failure` are separate outcomes. `execution_policy_block` is a bootstrap-level outcome before any installer attempt. An overall pass requires both installed-app smoke/uninstall passes, no developer runtime on guest PATH, and a successful real Defender capability probe. This remains an installation/startup harness, not full media-management workflow certification.

## Verification performed

`node --test packaging/windows-sandbox.test.cjs`: **7 passed, 0 skipped**. The tests execute real host preparation with harmless fake installer bytes, reject wrong hashes/stale versions/path traversal/missing exact artifacts despite newer decoys, execute the generated XML bootstrap against a harmless private script, classify distinct failures, and exercise actual Windows child processes with fast zero/nonzero exits and timeout termination. No installer, production guest body, or Sandbox is launched by the focused tests. Temporary test paths are checked against their dedicated workspace parent before cleanup.

Windows PowerShell 5.1 on the host has all execution-policy scopes Undefined (effective Restricted), while the installed PowerShell 7 has LocalMachine RemoteSigned. Tests and host preparation use the already-permitted PowerShell 7; Windows PowerShell guest-function/bootstrap tests use its unchanged defaults. The tests clear inherited PowerShell 7 module search paths when launching Windows PowerShell so that it initializes its own standard module paths; they do not alter execution policy.

## Final artifact run

Final run directory: **`packaging/build/windows-sandbox/20260906T201604184Z-d9e5ec2a`**. The host launched at 20:16:05 UTC. At 20:16:15 UTC, the bootstrap recorded **`execution_policy_block`**, phase **`guest_script_launch`**, exit **1**, and no guest result. The effective guest Windows PowerShell policy was **Restricted**, with every scope Undefined. The ordinary `-File` launch was denied before any installer ran. **Clean-Sandbox installation, bundled-runtime probes, app smoke, Defender scan, and uninstall are unverified in this environment. This is not an acceptance pass.**

`results/bootstrap-installers.json` independently rehashes both exact 0.4.1 files inside the guest and records both Authenticode statuses as `NotSigned`. The guest token log shows enabled Administrators membership and High Mandatory Level (`S-1-16-12288`). `bootstrap-defender-service.log` reports WinDefend `STOPPED` (Win32 exit 1077). CiTool returned the raw `OperationResult` **-2147009597**, so the policy inventory is not claimed successful. Both queried Code Integrity/AppLocker channels were available and contained **zero matching events** since bootstrap start; there is no claim of a 0.4.1 installer App Control event because installers were never launched.

Manifest SHA256: `e594863214a0a6712f431f7aa21501f5af7763746bd9fbb4e2efb590b899cee2`. Relevant evidence files are `host-result.json`, `results/bootstrap.json`, `results/bootstrap-exit.json`, `results/guest-console.log`, `results/bootstrap-installers.json`, `results/bootstrap-token.log`, `results/bootstrap-defender-service.log`, `results/bootstrap-app-control-policies.log`, and `results/bootstrap-events.json`. `session-cleanup.json` records closure of only this test's remote session, matched by its exact configuration path and launcher parent PID. The Sandbox service and Hyper-V VMs were not stopped or modified.

The preceding 0.4.1 run `20260906T201158231Z-de895926` is retained. It hit the same default policy block, but native console line wrapping caused the initial classifier to use `guest_script_not_started`. The final rerun verified the corrected classification and added guest-side installer hash/signature evidence. Both attempts used unchanged artifacts and security settings.

Final harness source SHA256 values:

| File | SHA256 |
| --- | --- |
| packaging/windows-sandbox-smoke.ps1 | c8656e59aaee6af42ae31015ea6bc791e886e8e455f1c88c0f1418208bf817f9 |
| packaging/windows-sandbox-guest.ps1 | 3a6996eaa2d036bf82da283635120c5ce20f2f0e49034e9ac77309839058a857 |
| packaging/windows-sandbox.test.cjs | 06f5f1ffa1d099f0cb28f9f3be5d066f22b31fc43c81c22c5b25a8424e967d08 |

## Archived 0.2.0 diagnostic evidence

The prior run `packaging/build/windows-sandbox/20260905T221246Z` is preserved unchanged and is not evidence for 0.4.1. Its two mapped installers failed at launch with an Application Control error. Code Integrity event **3077** names each exact 0.2.0 file/hash, policy `VerifiedAndReputableDesktop`, policy GUID `{0283ac0f-fff1-49ae-ada1-8a933130cad6}`, and status `0xc0e90002`. Associated events include 3033, 3089 and 3118. This is direct App Control enforcement evidence, not an inferred installer crash or missing development runtime.

The archived token reported administrator membership. Its `sc query WinDefend` output reports `STOPPED`, and event 3118 includes `DefenderDisabled=true`. Separately, `Get-MpComputerStatus` and CIM queries returned access denied. The archived native diagnostic exit-code fields were null and cannot be treated as success; the retained-handle runner fixes that evidence defect. These findings motivate diagnostic separation and guest-local copying, without claiming that copying will bypass or resolve policy enforcement.

Hyper-V fallback ownership and clean-baseline preservation remain with the root/VM worker. This harness does not modify those VMs or read/write a physical drive.
