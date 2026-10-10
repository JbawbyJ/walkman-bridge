# Clean Windows acceptance VM

This fallback owns only `Red-Lotus-Windows-Acceptance` and its files under the
workspace's `hyperv/Red-Lotus-Windows-Acceptance` directory. It never adopts,
starts, stops, reconfigures or restores the existing `RL-Founder-Dev`,
`RL-HC-Base-Candidate-20260731` or `RL-TEST-SITE` VMs. No physical Sony disk is
attached. Host security policies, trust stores and firewall settings are unchanged.

## Creation evidence — 2026-09-06

The new VM exists and is **off, with no operating system installed**. It is not
ready for clean Windows product acceptance. Its ID is
`055f9b68-4d8c-4b3d-ba7d-296086ee1772`.

Verified configuration: generation 2, four virtual CPUs, dynamic memory with
4 GiB startup/minimum and 8 GiB maximum, one dynamic 64 GiB VHDX, Microsoft
Windows Secure Boot template, virtual TPM, and the existing Default Switch.
Automatic startup/checkpoints are disabled; checkpoints require ProductionOnly.
The start action requires at least 6 GiB free host memory, retaining a 2 GiB
reserve after the 4 GiB startup allocation. It never stops another VM to free RAM.

The attached disk must be the exact recorded base disk in this VM's `disks`
directory. After a verified clean checkpoint, a differencing chain is accepted
only when its recorded checkpoint identity matches, every parent remains in that
same directory, and the chain ends at the original standalone dynamic base.
Sibling VM disks, external parents, cycles and unrecorded checkpoint disks are
rejected. The ordinary host harness requires exactly one DVD, its recorded
path in `hyperv/media`, and a freshly recomputed official ISO hash. The separately
reviewed first-installation coordinator described below admits two exact DVDs.

`hyperv/host-before-create.json`, `hyperv/host-create-verification.json`, and
`hyperv/Red-Lotus-Windows-Acceptance/ownership.json` retain host evidence.
Duplicate creation, checkpointing an off/uninstalled guest, and attaching an
invalid media file were exercised through the script and refused.
The three pre-existing VM identities, paths, states and CPU allocations remained
unchanged. The new guest's Defender, Windows setup and checkpoint are unverified.

The scripts parse and the host inventory/create paths ran under the existing
PowerShell 7 environment. Windows PowerShell 5.1 `-File` execution was rejected
by the host's existing script execution policy. That policy was not changed or
bypassed. Use an already permitted host PowerShell environment; an enforcement
failure is a blocker to report, not a reason to alter trust or enforcement.

## Official installation media

The initial inventory found no usable Windows ISO in Downloads, workspace tools,
`D:\Hyper-V`, or `D:\RL-VM`. On 2026-09-07 the official EN-US evaluation ISO was
downloaded and its complete size and hash verified. Evidence is in
`hyperv/media/download-verification-20260907.json`. Existing VM disks and Ubuntu
ISOs are not clean Windows substitutes.

Microsoft's [Windows 11 Enterprise evaluation page](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise)
lists registration as a prerequisite for the 90-day evaluation. Its registration
route opens the [registration form](https://info.microsoft.com/ww-landing-windows-11-enterprise.html).
The user must complete that interaction with their own details or confirm an
existing registration. No invented identity or unattended registration is used.
That registration is separate from guest account setup. Microsoft's documented
unattended `UserAccounts` settings can create a generic local test account;
personal Microsoft account credentials are not a technical prerequisite for
this deployment. See the supported preparation path below.

The prepared media is the EN-US x64 Enterprise 25H2 evaluation offered on the
[official download page](https://www.microsoft.com/en-us/evalcenter/download-windows-11-enterprise).
The observed Microsoft alias is `https://aka.ms/Win11E-ISO-25H2-en-us`; it resolves
to `https://software-static.download.prss.microsoft.com/dbazure/888969d5-f34g-4e03-ac9d-1f9786c66749/26200.6584.250915-1905.25h2_ge_release_svc_refresh_CLIENTENTERPRISEEVAL_OEMRET_x64FRE_en-us.iso`.
The expected size is 7,092,807,680 bytes and the official SHA256 is:

```
A61ADEAB895EF5A4DB436E0A7011C92A2FF17BB0357F58B13BBC4062E535E7B9
```

This value was read from Microsoft's [hash reference PDF](https://aka.ms/Win11-Hash-PDF),
saved as `hyperv/media/Verify-Download-Win11-Enterprise.pdf`. The PDF itself has
SHA256 `0D44BC561AF90844C0A0DA5DDC420F5FA84459872C02A1DEF6240FCFE1AAC2C7`.
Downloading a file is not verification: `AttachMedia` recomputes the complete ISO
hash and rejects a mismatch before changing the VM. A future Microsoft media
refresh requires rechecking the primary reference and updating this pinned hash.

## Prepare and verify the guest

The commands in this section describe the original interactive, one-DVD path.
The current VM has a prepared auxiliary answer DVD; use the first-installation
coordinator below instead of repeating `Create`, `AttachMedia`, or ordinary `Start`.

Run the following from the repository in an already permitted elevated host
PowerShell environment. `Create` is shown for reproducibility; it deliberately
refuses now because the exact VM already exists.

```powershell
.\packaging\hyperv-acceptance.ps1 -Action Inventory
# Only on a host without the owned VM or target directory:
.\packaging\hyperv-acceptance.ps1 -Action Create

# Place the registered official download under workspace\hyperv\media first.
.\packaging\hyperv-acceptance.ps1 -Action AttachMedia -IsoPath ..\hyperv\media\Windows11-Enterprise-Eval-25H2-en-us.iso
.\packaging\hyperv-acceptance.ps1 -Action Start
```

Use Hyper-V Manager/VMConnect to complete normal setup in this exact VM. Install
only Windows, finish Windows Update, update Defender signatures, run its quick
scan and restart when requested. Keep the Default Switch connected for those
updates. Do not install Node, Python, Java, FFmpeg, Git, SDKs, development tools or
either product before the baseline. Do not disable Defender, SmartScreen or app
control to make a test pass. Setup, registration and account choices requiring
the user's identity remain user interactions.

Once setup is complete, a guest administrator credential can be supplied in
memory through the standard prompt:

```powershell
$AcceptanceCredential = Get-Credential -Message 'Administrator account inside Red-Lotus-Windows-Acceptance'
.\packaging\hyperv-acceptance.ps1 -Action VerifyClean -GuestCredential $AcceptanceCredential
.\packaging\hyperv-acceptance.ps1 -Action Checkpoint -GuestCredential $AcceptanceCredential
Remove-Variable AcceptanceCredential
```

[PowerShell Direct](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/powershell-direct)
requires a running configured guest and valid guest credentials. It needs no
host share or inbound guest network configuration. The harness never saves
credentials. Its companion `hyperv-acceptance-guest.ps1` only inventories the
guest; it does not install software, alter policy, or change Defender settings.

The baseline must prove Windows 11 Enterprise Evaluation x64, administrator
access, Secure Boot, a ready TPM, active Defender with current signatures, no
pending servicing reboot, and no developer/product installations. PATH checks
exclude WindowsApps execution aliases and are supplemented with uninstall
registry and common installation directory checks. Unknown query failures fail
the baseline rather than being interpreted as disabled/absent software.

`Checkpoint` reruns these checks immediately before creating
`Factory-Windows11-Defender-Clean`. It records the returned checkpoint ID and
baseline evidence only after Hyper-V confirms creation. It never replaces an
existing checkpoint and exposes no restore or delete command. A VM shell, an ISO
download, a successful boot, or an unverified checkpoint is not a ready factory
guest. [Microsoft's checkpoint guidance](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/checkpoints)
explains the distinction between production and standard checkpoints.

## Supported unattended preparation (2026-09-07)

`packaging/prepare-acceptance-unattend.ps1` is preparation only. Its default
`Describe` action reads no credential or VM. `Prepare` verifies the pinned ISO
hash and fixed ownership record, then creates a fresh private package inside
`hyperv/Red-Lotus-Windows-Acceptance/bootstrap`. It never imports Hyper-V or
mounts, attaches, starts, modifies or deletes a VM, disk or installation ISO.
Existing bootstrap material, including a failed attempt, blocks a second prep.

Microsoft documents [answer-file discovery on read-only removable media](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/windows-setup-automation-overview?view=windows-11):
the root file must be named `Autounattend.xml` and contain settings for the
current pass. The prep creates that one root file on a nonbootable auxiliary
ISO using Windows' [IMAPI image API](https://learn.microsoft.com/en-us/windows/win32/api/imapi2fs/nf-imapi2fs-ifilesystemimage-createresultimage).
No ADK or guest development tool is needed. Real IMAPI output is checked by an
independent ISO9660 directory reader in the focused tests. Actual discovery by
the target Win11 installer remains a guest acceptance check; if discovery fails,
the documented WinPE `setup.exe /unattend:<path>` route is available for review.

The answer uses standard `windowsPE`, `specialize` and `oobeSystem` settings,
selecting `Windows 11 Enterprise Evaluation` by WIM image name. Metadata may be
verified using DISM or a reliable reader of the WIM XML header from the hash-bound
official ISO; DISM is not a special permission or licensing prerequisite. The
caller must record that verification before invoking `Prepare`. The observed
official image is index 1, edition `EnterpriseEval`, amd64, build 26200.6584.

```powershell
# Run in the existing permitted PowerShell 7 host. These commands prepare files;
# they do not attach media, consent to registration, or run Windows Setup.
.\packaging\prepare-acceptance-unattend.ps1
.\packaging\prepare-acceptance-unattend.ps1 -Action Prepare `
  -InstallationIsoPath (Join-Path (Resolve-Path ..).Path 'hyperv\media\Windows11-Enterprise-Eval-25H2-en-us.iso') `
  -ImageName 'Windows 11 Enterprise Evaluation'
```

The answer creates the generic local administrator `RedLotusAcceptance` with a
random 256-bit password. This is a test-machine identity, not a claim about a
person. [Microsoft's account-creation documentation](https://learn.microsoft.com/en-us/windows-hardware/customize/desktop/unattend/microsoft-windows-shell-setup-autologon)
states that an account created through `UserAccounts` skips the account-creation
phase of OOBE. The answer uses `HideOnlineAccountScreens`, has no AutoLogon,
`SkipMachineOOBE`, bypass commands, remote account or product key, and preserves
UAC, Defender, SmartScreen and app control. `ProtectYourPC=1` uses Microsoft's
express protection settings; it does not choose the commonly copied value 3
that turns those settings off. The local account is sufficient to supply a
`PSCredential`; the operator need not provide personal Microsoft credentials.

The answer sets [`AcceptEula=true`](https://learn.microsoft.com/en-us/windows-hardware/customize/desktop/unattend/microsoft-windows-setup-userdata-accepteula),
the documented unattended license-acceptance setting. Preparing XML does not
execute acceptance. Root must settle the actual media/licensing path before
starting Setup. Microsoft's public download page and ISO alias answered anonymous
HEAD requests with HTTP 200 on 2026-09-07, but this technical accessibility does
not waive the evaluation page's stated registration prerequisite. Any actual
registration/account/MFA interaction must use genuine authorized details.

The package contains `answer.iso`, `credential.xml` and a nonsecret `manifest.json`.
Its directory is created atomically with inheritance disabled, granting only the
creating user and SYSTEM full access. `credential.xml` uses Windows CurrentUser
DPAPI through `Export-Clixml`; another user or machine cannot normally import it.
The XML is assembled in memory and passed directly to IMAPI, so there is no
standalone plaintext answer file in a staging directory. The ISO itself still
contains the recoverable password. [Hiding passwords in an answer file is not encryption](https://learn.microsoft.com/en-us/windows-hardware/customize/desktop/wsim/hide-sensitive-data-in-an-answer-file).
Keep the entire package out of source control, release archives, shares and logs.
The verifier permits a later read-only grant for this exact VM identity on
`answer.iso` only; it never grants that access itself or permits VM access to
`credential.xml`. Hyper-V also adds a fixed worker AppContainer capability ACE
after attachment. This capability is not a VM identity. The verifier admits it
only on `answer.iso`, paired with the exact VM SID, with both grants explicitly
`Read,Synchronize`, no inheritance or extra rights. Unknown principals, missing
VM grants, and capability access on the manifest, credential or directories are
rejected. Microsoft's [dual-principal access model](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)
requires both normal identity and AppContainer access; its
[Hyper-V identity guidance](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn741279(v=ws.11))
identifies each VM worker's primary account as that VM's SID. Query-only access
to the actual worker tokens was denied, so their current token layout was not
directly inspected. Do not grant broad access to Hyper-V's all-VMs group.

```powershell
# Use only the package path returned by Prepare. Do not print the credential.
. .\packaging\prepare-acceptance-unattend.ps1
$AcceptanceCredential = Get-AcceptanceCredential -PackagePath $PreparedPackagePath
# Root passes this in memory to PowerShell Direct after normal setup completes.
# Remove-Variable AcceptanceCredential when coordinated guest work is finished.
```

The answer partitions a single blank 64-GiB guest disk 0 as EFI (512 MiB), MSR
(16 MiB), Windows (63,744 MiB) and recovery (the remainder), following the
[UEFI partition layout](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/configure-uefigpt-based-hard-drive-partitions?view=windows-11).
[`WillWipeDisk=false`](https://learn.microsoft.com/en-us/windows-hardware/customize/desktop/unattend/microsoft-windows-setup-diskconfiguration-disk-willwipedisk)
avoids erasing all existing partitions first. It is not a substitute for a blank
disk preflight: subsequent formatting uses explicit partition IDs. The host
coordinator must bind the exact owned standalone VHD, controller/slot, size and
captured initial SHA256 immediately before first boot, reject other disks, and
record a one-time bootstrap start before starting the VM. Do not automatically
retry this installation recipe after a partial boot.

The existing host harness's one-DVD guard is unchanged. The reviewed
`packaging/acceptance-bootstrap.ps1` coordinator admits exactly the unchanged
official installer ISO plus this package's hashed answer ISO, preserving
VM/disk/media identity checks and the host RAM reserve. After first boot, the
operator must prove Setup completion, verify the local account works, remove the cached answer XML
from the exact Windows Setup locations, and verify no unattended password or
autologon remains. Only then detach the answer DVD and, after confirmed detachment
and scoped path checks, delete the exact private answer ISO. Credential retention
or deletion is a separate coordinated step. This prep exposes no deletion action
and keeps partial files private for recovery. It cannot certify secure erasure
of SSD blocks or host/guest snapshots.

Run focused host tests with:

```powershell
pwsh -NoProfile -File .\packaging\prepare-acceptance-unattend.test.ps1
```

The tests create and remove only their own private synthetic fixtures. They use
real DPAPI, NTFS ACLs, a Windows junction and IMAPI; the full-flow test substitutes
only the external official-media hash. They do not prove installed Windows,
Defender readiness, clean checkpoint creation or product acceptance.

Windows Sandbox has no documented Defender-enablement setting in its
[supported WSB configuration](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file).
Microsoft's [`MP_FORCE_USE_SANDBOX` guidance](https://learn.microsoft.com/en-us/defender-endpoint/sandbox-mdav)
isolates Defender's own content process on an already protected OS; it does not
install Defender into Windows Sandbox. No host security setting should be changed
on that basis. Use affirmative Defender status and a real scan in the full guest;
absence or ambiguous output remains a failed acceptance prerequisite.

## First-installation coordinator

`packaging/acceptance-bootstrap.ps1` defaults to `Describe`, with no VM actions.
`Attach` verifies the owned, powered-off VM, its sole initial 64-GiB standalone
disk on SCSI 0:0, no checkpoints, and the complete media hashes. It records the
initial disk hash and identifier before any installation is admitted. The
current machine has already completed Attach; do not repeat it or recreate its
private preparation package.

```powershell
.\packaging\acceptance-bootstrap.ps1
# For a new preparation only; current VM is already attached:
.\packaging\acceptance-bootstrap.ps1 -BootstrapAction Attach -PackagePath $PreparedPackagePath
# Only after the owner confirms Microsoft evaluation registration:
.\packaging\acceptance-bootstrap.ps1 -BootstrapAction Start -PackagePath $PreparedPackagePath -EvaluationRegistrationConfirmed
```

Start requires the exact recorded package and both DVDs, unchanged initial disk,
registration confirmation, and 6 GiB free host RAM. Admission is persisted before
`Start-VM`. A failed start retains that admission for diagnosis and cannot be
automatically replayed. Other VMs and host security policy remain untouched.
The preparation manifest is immutable and retains `prepared_not_attached`;
`ownership.json` records the VM's actual `bootstrap_attached`/`bootstrap_started`
state. Neither field establishes successful Windows installation.

Run `packaging/acceptance-bootstrap.test.ps1` for the synthetic coordinator checks
and `packaging/acceptance-acl.test.ps1` for the real NTFS ACL regression cases.
The current environment observations and remaining acceptance work are recorded
in [ENVIRONMENT-20260907.md](ENVIRONMENT-20260907.md).

## Product acceptance handoff

Run `python packaging/verify-release.py` with Node.js available on PATH before
transferring products. It derives the current release identities from
`package.json` and `products.cjs`, verifies complete resource manifests and
source/ASAR parity, checks product exclusions and component licenses, and
preserves the historical 0.4.0 installer hashes. Its report is
`packaging/build/branding-041/artifact-proof.json`. This read-only artifact check
writes only its report and does not establish clean-guest runtime acceptance.

Root coordinates the final 0.4.1 installer tests only after the clean checkpoint
is verified. Transfer only final hash-verified installers and the approved test
harness into the guest using PowerShell Direct. Keep input/output inside a
dedicated guest test directory and export the original logs and result JSON.
Run installed-product smoke, bundled-runtime checks, real application workflows,
uninstall and post-run Defender checks; do not count a host test as guest proof.
Preserve any policy denial as evidence. Reverting to the clean checkpoint is a
separate coordinated VM mutation, not an automatic side effect of this harness.
