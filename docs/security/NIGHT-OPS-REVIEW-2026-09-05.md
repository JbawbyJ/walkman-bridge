# Night Ops security review — 2026-09-05

This bounded review covered the in-progress working tree for Red Lotus Player and Walkman Bridge 0.2.0. It examined the privileged scanner protocol, Defender clearance policy, media content leases, loopback authentication, renderer IPC and shutdown behavior against `docs/IMPLEMENTATION-CONTRACT.md`. It was direct source review plus safely executed tests; the dedicated Security Review runner was unavailable. No malware, physical Walkman operation, commit or publication was performed by this reviewer.

The native fixes address the concrete attack paths found in this pass. This is not final installer sign-off: installers were being rebuilt while the report was prepared, and two security-related components were subsequently implemented by this reviewer as explicitly assigned below. Those components need another reviewer.

## Findings and repairs

| Initial severity | Location | Finding and disposition |
| --- | --- | --- |
| High | `electron/scanner-broker.cjs`, original Node pipe exchange | The broker trusted an echoed helper PID without asking Windows which process owned the named-pipe server. A benign local pipe server reproduced acceptance of a forged result whose claimed PID differed from the real server PID. Replaced by the native `--exchange` process in `ScannerHelper/NativeExchange.cs`: it owns `RunAs`, retains the actual elevated process handle, checks that process is alive, and calls `GetNamedPipeServerProcessId` before announcing readiness and again immediately before writing request secrets. The elevated server verifies the exchange client's OS PID. |
| High | Helper runtime configuration and launch environment | Inherited `.NET` startup-hook/profiler settings could execute supplied code before the helper entry point, including before its argument validation. A harmless marker DLL executed against the old published helper with invalid arguments in a normal-privilege test. Native worker disabled startup-hook/debugger support in the published runtime and stripped runtime-hook/profiler environment prefixes at broker/native launch boundaries. The same class of marker probe did not execute against the rebuilt published helper. |
| High | `ScannerHelper/DefenderScanner.cs`, original PowerShell status query | Bare `powershell.exe` resolution and default module auto-loading exposed the privileged status query to executable/module substitution. The native worker now uses the absolute OS PowerShell executable, OS module paths, disabled auto-loading, explicit Defender/Utility imports, module-qualified commands and a sanitized environment/working directory. Root separately hardened the ordinary-user Python fallback the same way. |
| Medium | `backend/scan.py`, original registry fallback | Installed signature versions and service/policy state were being used to invent active, real-time-protection and Normal-mode status. Those facts do not establish current Defender protection. The fallback now obtains explicit Windows Security Center product state/signature status, merges actual version evidence, and fails closed when the selected policy cannot be established. Details below. |
| High | `electron/main.cjs`, backend exit after startup | Exit rejected an already-resolved startup promise but left the renderer, session capability and old origin usable. A different local process could occupy the freed port. A deterministic execution of the original main entry point reproduced continued outgoing-origin permission with no broker stop, cookie removal or window destruction. This reviewer implemented the authorized repair: a synchronous loss latch, direct child-liveness checks, aborted main/broker work, renderer destruction, cookie revocation and an explicit unknown-transfer/deletion outcome dialog. |
| Low | Public media/job scan serialization | Scanner-private path and request metadata were reaching public renderer records. Root added public scan/job projections. Private scanner routes still require the native header capability, not the renderer cookie. |

The original scanner implementation was by Daybreak and was preserved before repairs. The native helper/process/PowerShell hardening was authored by the native worker, originally assigned as the UI worker. Root integrated the backend policy, public projections and media/operation flow changes. This reviewer authored `backend/defender_status.py` and its focused tests, then the explicitly assigned Electron lifecycle/broker cancellation repair and acceptance harness. Those are implementation contributions, not independent sign-off on the same code.

## Defender policy and file identity

The normal-user status reader uses typed Windows Security Center COM interfaces, with balanced COM initialization, interface release and BSTR cleanup. It selects exactly one product with the exact remediation identity `windowsdefender://`; a display name alone is insufficient. Missing, ambiguous or malformed identities/states fail closed. The WSC path is restricted to supported Windows clients and 64-bit execution. Unsupported WSC environments do not receive invented status; another supported authoritative query must succeed or clearance fails.

For WSC evidence, policy requires `available`, `product_state == ON` and `signature_status == UP_TO_DATE`, together with actual engine/signature/platform versions. Registry and service/policy checks supplement that live product state; they do not substitute for it. WSC `ON` is used as its documented active-protection state. It is not relabeled as `RealTimeProtectionEnabled` or `AMRunningMode`. The product state timestamp is a state-change timestamp, not a heartbeat or proof of fresh polling. The separate trusted PowerShell path requires its actual active/RTP/Normal-mode fields.

The supporting primary references are Microsoft's [product-state definition](https://learn.microsoft.com/en-us/windows/win32/api/iwscapi/ne-iwscapi-wsc_security_product_state), [signature-state definition](https://learn.microsoft.com/en-us/windows/win32/api/iwscapi/ne-iwscapi-wsc_security_signature_status), [supported client API](https://learn.microsoft.com/en-us/windows/win32/api/iwscapi/nf-iwscapi-iwscproductlist-initialize) and [published SDK interface declarations](https://raw.githubusercontent.com/microsoft/win32metadata/main/generation/WinSDK/RecompiledIdlHeaders/um/iwscapi.h). The typed ABI follows the actual `IDispatch` vtable slots in the SDK declarations.

The helper runs only fixed Defender custom-file scan arguments, including `-DisableRemediation`. It validates managed-root containment, rejects reparse paths, resolves the opened file handle, denies concurrent write/delete sharing, and compares content identity before and after scanning. Backend clearance binds explicit clean, policy version, bounded age, digest/size and current Defender versions. Source and generated derivative admissions retain the corresponding content/operation leases. An explicit clean outcome is required before media decoding, playback or transfer.

## Independently executed evidence

The reviewed published helper SHA-256 was read again during this report:

`1EFFA309F93323AA6C6695C68B4F219990923AEF5FB908EA7DF0BAF83E8CBA20`

Path: `scanner-helper/artifacts/win-x64/nightops-scanner-helper.exe`.

- Native helper test executable: **9 passed**, including a real wrong-server-PID test that sends zero secret bytes. This reviewer executed the tests without UAC.
- Published-helper benign startup-hook regression: invalid-argument launch returned exit 1 / `scanner_exchange_failed`, and the marker did **not** execute. The temporary marker DLL/profile was removed. This was a normal-privilege probe, not a claim that an elevated malicious payload was executed.
- Focused backend scan/bridge/store/WSC test run: **66 passed, 1 skipped**. Test execution passed; its wrapper initially encountered a temporary SQLite cleanup lock, which was removed after process exit. It is not represented as an exit-zero wrapper run. The WSC tests include code authored by this reviewer.
- A native normal-user WSC probe returned the actual Defender product with `ON` and `UP_TO_DATE`. WMI and late-bound COM attempts were unavailable in this host context; the typed native interface worked. This validates operation on the current host, not a Windows-version matrix.
- `npm run test:electron`: **25 passed** after the lifecycle repair: 8 lifecycle, 13 broker and 4 boundary tests. New regressions first failed against the original behavior. They cover late HTTP/helper continuations, startup/health/cookie races, actual child-state checks before exit callbacks, Stop-ACK failure, renderer crash during ACK, cookie/dialog ordering and confirmed idle shutdown.
- Actual Electron **44.2.0** acceptance: **passed, process exit 0**, using `electron/tests/lifecycle.electron.cjs`. Real isolated BrowserWindow/session/preload/IPC ran the production main and broker modules against a temporary authenticated HTTP fixture. No production scanner bypass was added. Window minimize/restore and maximize/unmaximize worked through production IPC. An HTTP 503 busy probe retained the live window/backend. A subsequent close waited **3152 ms** over three busy responses and a final idle response, then revoked the cookie, destroyed the window and performed exactly one expected backend termination.

Actual lifecycle evidence: `packaging/build/electron-lifecycle-acceptance/proof.json`, `stdout.log`, `stderr.log`. Temporary browser profiles were removed after the process exited. A first sandboxed attempt could not start the Windows renderer/GPU children; the successful run used the same authorized unsandboxed hidden-process launch as the desktop smoke tests. The successful run emitted one GPU shutdown diagnostic after all assertions passed; it did not prevent the lifecycle checks.

To repeat the actual lifecycle harness on this host, launch `node_modules/electron/dist/electron.exe` with argument `electron/tests/lifecycle.electron.cjs` from the repository root, using `Start-Process -WindowStyle Hidden -Wait -PassThru` and separate stdout/stderr files. The harness reports failures to JSON and exits nonzero. Windows process-launch restrictions may require the authorized unsandboxed execution context. It creates only its own temporary profile and authenticated loopback fixture; it never starts Python, a scanner or device work.

## Attributed integration evidence

These artifacts were inspected, but the corresponding runs were performed by root/native worker, not independently repeated by this reviewer:

- `scanner-helper/artifacts/real-uac-proof/real-uac-smoke.json`: user-approved fresh run with two harmless WAV requests and the single real elevated helper PID **31384**; both returned explicit Defender `CLEAN`, with no cancellations/errors reported. This fresh run is distinct from Daybreak's earlier UAC smoke.
- `packaging/build/real-media/real-media.json`: actual ordinary Defender source clearance, scanned generated FLAC, ranged source/FLAC reads and successful media duration; no device modules loaded. Root also reported a current normal-user WSC clean/revalidation probe.
- `packaging/build/source-smoke/Red Lotus Player/nightops.log` and `packaging/build/source-smoke/Walkman Bridge/nightops.log`: both actual Electron source smokes passed authenticated startup, correct product, cookie isolation, renderer denial of native scanner routes and graceful drain. Player reported Java/JAR absent; Bridge reported them present. Both reported no connected device.

## Remaining limits and release gates

No additional confirmed exploitable native-protocol blocker was identified in the inspected fixes. The following limits remain material:

- A different reviewer must assess the WSC reader and lifecycle/broker changes authored by this reviewer. Root's source inspection and an independent verifier were in progress; this report does not claim their unfinished results.
- The rebuilt final installers and their installed contents were not the target of this pass. Final artifact verification must confirm that they contain the reviewed source/runtime and helper hash. Physical Walkman recovery and real device-write behavior remain human-controlled acceptance work.
- The helper's Authenticode status was **NotSigned** when queried. The SHA-256 above identifies the inspected bytes; it provides neither verified publisher identity nor protection against replacing a per-user writable installation. Signing/provenance remains a release-owner decision. This review does not claim resistance to an attacker already able to rewrite the installed application as the same user.
- Malicious scan outcomes, UAC cancellation and unavailable Defender states use safe test seams; no malware was executed. Successful normal-user WSC behavior on this host does not establish availability on unsupported Windows editions or every Defender configuration.

This is the Codex security-review pass only, not a full product penetration test. Minimum companion gates are **@bug-hunter** and **@farm-verifier**. Before shipping security-sensitive work, preserve this report with the release evidence and obtain cross-model review: **Codex Sol** primary AppSec and **Claude Opus** independent review, never Fable. No additional fleet was spawned by this reviewer.

## Integration close-out by root

After this review was written, the independent device verifier inspected the lifecycle/broker repair and reran all25 Electron regressions with no new blocking finding. Root separately read the complete WSC COM implementation and lifecycle integration, ran the full154-test backend suite (including WSC tests; one symlink-environment skip), and exercised real ordinary Defender source/FLAC clearance. These checks cover the self-authored components above without claiming another external model fleet was used.

Root also compared both packaged backend/frontend trees and the reviewed helper bytes with the final source, and extracted the ASAR main/preload/broker/boundary files for byte equality. Installation evidence and final build hashes are tracked in ../IMPLEMENTATION-PROGRESS.md and packaging/build/install-smoke. The release-only, signing, clean-machine and physical-device limits above remain explicit.

The final host installations/uninstalls passed. A subsequent fresh Windows Sandbox booted and verified the same installer hashes, with no developer tools on PATH, but Windows Application Control blocked both unsigned installers before execution. This is an OS policy block, not a Codex approval-review rejection. No policy was disabled or worked around. Guest evidence is packaging/build/windows-sandbox/20260905T213005Z/results/guest-result.json; clean-guest installation acceptance remains unproven.
