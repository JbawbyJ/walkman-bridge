# Windows acceptance environment — 2026-09-07

**Preparation and attachment pass. Windows installation and product acceptance
remain pending.** The dedicated `Red-Lotus-Windows-Acceptance` VM is powered off,
with the verified official Windows installer and private answer media attached.
No application binaries or the existing 0.4.1 deployment ZIP were rebuilt.

## Completed work

- Downloaded Microsoft's Windows 11 Enterprise Evaluation 25H2 EN-US x64 ISO:
  7,092,807,680 bytes, SHA-256
  `A61ADEAB895EF5A4DB436E0A7011C92A2FF17BB0357F58B13BBC4062E535E7B9`.
  Independently read the hash-bound WIM header: image 1, EnterpriseEval, amd64,
  build 26200.6584. The ISO is no longer mounted on the host.
- Prepared a supported unattended setup package with a generic local test
  account, a random password, CurrentUser DPAPI credential storage, and private
  NTFS permissions. The answer ISO contains the recoverable password and stays
  outside source control, deployment archives and logs.
- Added the first-installation coordinator. It binds the exact VM, initial disk,
  both media images and preparation package. It preserves a 6 GiB free-memory
  guard and records one-shot admission before starting Windows writes. Failed
  starts cannot silently replay the installation.
- Fixed the post-attachment permission validation failure: Hyper-V adds a fixed
  AppContainer capability to the answer ISO. The verifier now requires that
  read-only capability to be paired with this exact VM's read-only identity on
  the answer ISO. The credential and manifest receive no VM/capability access.
- Ran 33 preparation, 39 coordinator and 26 ACL checks: **98 passed**. The ACL
  regression first failed against the prior verifier and passed after the fix.
  Daybreak reviewed the security-sensitive preparation and coordinator changes.

The ACL interpretation follows Microsoft's documented
[AppContainer dual-principal model](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)
and [per-VM worker account](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn741279(v=ws.11)).
It does not treat the fixed capability as a VM-specific SID. Read-only queries of
the current worker tokens were denied; token placement was not directly inspected.

## Actual postflight

Observed at 2026-09-07T17:26:50Z; resource values can change.

| Check | Result |
| --- | --- |
| Dedicated VM identity | `055f9b68-4d8c-4b3d-ba7d-296086ee1772`, powered off |
| Configuration | Generation 2, four CPUs, 4–8 GiB dynamic RAM, Secure Boot, vTPM, Default Switch |
| Installation disk | Sole initial 64 GiB disk; recorded identifier and hash unchanged |
| DVD media | Exactly the verified installer and private answer ISO |
| Private package | Hashes and ACLs verified after Hyper-V attachment |
| Other three VMs | Identities, states, paths and CPU allocations match the creation baseline |
| Host RAM | 5.36 GiB free; 6 GiB required to start |
| Host Defender | Running; antivirus, real-time protection and tamper protection enabled; signatures `1.459.91.0` |
| Guest Windows / Defender / checkpoint | Not yet verified; Windows has not started |
| Clean-guest installers / real UAC clearance | Not yet tested |

The initial post-attach ACL rejection is retained as evidence. The corrected
postflight changed no ACL, VM, host policy or existing workload.

## Remaining work

1. Obtain the owner's confirmation of the Microsoft evaluation registration
   prerequisite. Microsoft's [download page](https://www.microsoft.com/en-us/evalcenter/download-windows-11-enterprise)
   still lists registration. The public download was accessible anonymously;
   this does not waive that prerequisite. No identity or registration was
   fabricated or submitted by the agent.
2. Free enough host memory for the retained 6 GiB start threshold. Existing
   workloads are preserved; the harness does not stop another VM.
3. Start the recorded preparation once, finish Windows Setup, update Windows and
   Defender, and perform a real guest scan.
4. Verify the local account, remove cached setup answer material in the guest,
   verify no setup password or autologon remains, and detach the answer ISO.
   Complete any scoped private-media cleanup only after confirmed detachment.
5. Verify and create the clean checkpoint, then test both final installers,
   bundled runtimes, application workflows and actual elevated Defender helper.

Commands and custody rules are in [HYPERV-ACCEPTANCE.md](HYPERV-ACCEPTANCE.md).
The earlier Windows Sandbox execution-policy and unavailable-Defender failures
remain separate evidence. No execution policy, Defender, UAC, SmartScreen or app
control was disabled or bypassed.

## Hermes consultation

Automatic approval review rejected the proposed detailed environment handoff
because it contained internal host/VM/security information for an unverified
external destination; that payload was not sent. A generic public Windows
question was then approved and submitted through the configured Hermes CLI.
Both attempts returned HTTP 400 (`tool_choice` set with no tools specified),
despite CLI exit code 0. No usable Hermes recommendation was obtained. Local work
and Daybreak review continued without exposing the detailed report.

## Evidence

- `packaging/build/environment-20260907/environment-result.json` — current actual
  postflight; no credentials or answer-file contents.
- `packaging/build/environment-20260907/post-attach-acl-observation.json` — initial
  integration rejection and read-only ACL metadata.
- `packaging/build/environment-20260907/windows-image-inventory.json` — WIM header.
- Workspace `hyperv/media/download-verification-20260907.json` — media provenance.
- [Bootstrap verification](security/BOOTSTRAP-20260907-REVIEW.md) and
  [Daybreak security review](security/UNATTEND-20260907-REVIEW.md).

The private preparation manifest intentionally remains immutable with state
`prepared_not_attached`; the VM ownership record carries the actual
`bootstrap_attached` state and `installation_started=false`. The older acquisition
and VM-creation snapshots are historical, not current download status.
