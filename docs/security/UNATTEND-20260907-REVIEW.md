# Acceptance Unattend Preparation Security Review

Security review found no issues in the frozen prep-only scope.

Reviewed artifacts and confirmed SHA-256 values:

- `packaging/prepare-acceptance-unattend.ps1`: `4C1074B417F00F1F33201ED26C8CD2BE90AF889748674FCA8A7DD50C964C999D`
- `packaging/prepare-acceptance-unattend.test.ps1`: `2DA820DDE6122EBC30B80A46FC7D4A15D278193C046D007B56EBB6335ADFD1D1`
- `docs/HYPERV-ACCEPTANCE.md`: `076AC86BB6B9FEFAB3942E5D9E652F69539DC3391B526FAE9C0994C76CAADC2A`

The review found no concrete reachable weakness in generated local-credential handling, DPAPI and ACL sequencing, IMAPI answer-media creation, XML value handling, failure logging, sensitive-media custody, path/ownership/hash guards, the exact-VM read-only exception for `answer.iso`, or the absence of security-disable and VM/disk mutation commands.

The permitted private synthetic test passed 33 cases. It exercised DPAPI, ACLs, IMAPI ISO creation and independent extraction, path guards, junction handling, and cleanup without VM or disk actions. The official-ISO hash boundary remained mocked in the full-flow test.

## Limits

No real bootstrap package was read or decrypted. No VM, disk, installer, device, network, or security-policy action was run. Real VM/disk identity, answer discovery, and installation remain unverified. `WillWipeDisk=false` does not make absolute partition IDs safe for an existing layout: the later coordinator must enforce the documented exact blank 64 GiB disk preflight and verify actual VM ownership and disk state before use.

This pass is Codex security-review only, not a full product penetration test. The minimum companion gates for security-touching work are `@bug-hunter` and `@farm-verifier`. Before shipping a security-sensitive Hermes/T3 slice, retain a durable report under `.hermes/plans/verify/` or `docs/security/` and obtain cross-model review with Codex Sol as primary AppSec reviewer and Claude Opus independently; never use Fable for that gate.

## Bootstrap and import-guard supplement

Security review found no issues in the final narrow scope covering `packaging/acceptance-bootstrap.ps1` (confirmed SHA-256 `64651609C89450E2369D2FDD17827E0773B9BF1549C1FFA3DE1DBBA0F4ADE3DF`) and the import-only guard in `packaging/hyperv-acceptance.ps1`.

The import guards return before Hyper-V import or action dispatch. Attach admits only the exact owned, powered-off VM with the fresh 64 GiB disk and grants the exact VM SID read access solely to `answer.iso`. Start requires explicit registration, unchanged media and disk evidence, sufficient RAM, and records its one-shot admission before invoking `Start-VM`; there is no automatic replay path.

The independent verifier reported 39 passing synthetic tests, and the coordinator reran those 39 plus the 33 prep tests. The bounded real Attach evidence recorded exactly two DVD devices, the VM powered off, the disk unchanged, and the answer ISO restricted to the exact VM read ACL. Start was not called because registration was unconfirmed and RAM was below 6 GiB.

Limits: both reviewed source files were untracked, so no committed baseline diff existed. This review did not execute VM, media, credential, disk, or policy actions. Actual Windows Setup, disk writes, answer cleanup, and Start remain unverified.

## Superseding answer-media ACL supplement

Security review found no issues in the frozen ACL correction.

Reviewed artifacts and confirmed SHA-256 values:

- `packaging/prepare-acceptance-unattend.ps1`: `26B28BC13B948E93E517FC7AA3D585BC6599ABA7155CFF5EA9EBB3CF45711395`
- `packaging/acceptance-bootstrap.ps1`: `E7CE58BD693522BEA2E56B7399EDEEE469E99E08ED5C2A9314B2A24E056DD37A`
- `packaging/acceptance-acl.test.ps1`: `2D9B1DAD824090D5BF175EB7255767F15E63EE364C3148CC11797DEA69384C44`
- `packaging/prepare-acceptance-unattend.test.ps1`: `2DA820DDE6122EBC30B80A46FC7D4A15D278193C046D007B56EBB6335ADFD1D1`
- `packaging/acceptance-bootstrap.test.ps1`: `DE11ED7EE804DF4F0E7C641DC141B2D5DF4FFCC164E697BB07F5ACFD56F316A0`

The fixed Hyper-V VMWP capability SID is treated as a global capability, not as a value derived from the VM. It is admitted only as an explicit, non-inherited `Read|Synchronize` ACE on the file whose basename is exactly `answer.iso`, and only when paired with the expected exact VM SID. Every matching VM grant must independently be explicit, non-inherited, and exactly `Read|Synchronize`. Capability-only admission and other identities, masks, inheritance, or locations remain fail-closed. The coordinator's initial answer-media grant was reduced from read-and-execute to read.

This conjunction follows Microsoft's documented model: `vmwp.exe` uses the exact VM SID as its primary account, while AppContainer and restricted-token access is the intersection of normal identity and capability/restricting-SID grants. The capability ACE therefore does not replace the exact VM identity check.

Synthetic verification passed 26/26 ACL cases, 33/33 preparation cases, and 39/39 coordinator cases.

Limits: the capability's SID is fixed and hash-derived from a capability name; it is not VM-derived. The actual VMWP token placement could not be inspected because the read-only token probe received `OpenProcess` access denied. No real VM, media, credential, disk, or policy action was run in this review, so the live answer ISO ACL remains unverified here.
