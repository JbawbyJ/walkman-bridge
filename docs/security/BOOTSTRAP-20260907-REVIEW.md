# Acceptance bootstrap independent review — 2026-09-07

**Scoped verdict: pass.** No unresolved concrete correctness or security defect was found in the reviewed coordinator and dot-source guard. Fresh execution passed **39 tests, zero failures**, on PowerShell 7.6.5 in the default tool sandbox. This is a coordinator verification result, not Windows installation or guest acceptance evidence.

The initial review identified a parameter collision: dot-sourcing the preparation script reset the coordinator's `PackagePath`. Root had already added save/restore protection by the time executable verification began. The current tests prove both Attach and Start preserve their supplied package. A controlled in-memory removal of that protection reproduces the original clobber, without calling any environment command.

## Scope and independent evidence

Reviewed production files:

- `packaging/acceptance-bootstrap.ps1`, SHA-256 `64651609C89450E2369D2FDD17827E0773B9BF1549C1FFA3DE1DBBA0F4ADE3DF`.
- The new dot-source guard in `packaging/hyperv-acceptance.ps1`; whole-file SHA-256 `30C29ADF6B0CB4D50D04DDDD7AFA92184BCC4F00EAA6314A6B4D6988CEB447F4`.

New independent test file: `packaging/acceptance-bootstrap.test.ps1`, SHA-256 `DE11ED7EE804DF4F0E7C641DC141B2D5DF4FFCC164E697BB07F5ACFD56F316A0`.

Executed from the repository root:

```powershell
& ./packaging/acceptance-bootstrap.test.ps1
```

The final run exited **0** with `passed: 39`, `failed: 0`. The earlier first run passed 35 checks; four focused regression/Attach checks were then added, and the entire current suite was rerun.

| Requirement or risk | Fresh evidence | Result |
| --- | --- | --- |
| Describe and dot-source have no VM effects | Real imports and entrypoint with environment commands intercepted; no calls recorded | Pass |
| Imported parameter blocks cannot erase the requested package/action | Attach and Start real dot-source parameter checks; production body forwards exact supplied package; regression control recreates old defect | Pass |
| A recorded attempt cannot be replaced, and unexpected attached DVDs are rejected | Existing-attempt, extra-DVD and unrelated-DVD Attach checks stop before any media operation | Pass |
| Exact VM, guest disk and configured machine | Different VM ID, guest disk index/capacity and failed configuration validation prevent admission | Pass |
| Sole fresh disk on SCSI 0:0 | Extra disk, wrong controller/type/index/location/path, capacity, expanded file, attached/fixed/parented disk all rejected | Pass |
| Disk cannot change between Attach and Start | Changed synthetic bytes and changed disk identifier rejected | Pass |
| Exact installation and answer media | Changed installation bytes, extra/replaced DVDs and changed recorded package path/manifest rejected | Pass |
| No first installation on an already active or checkpointed VM | Running VM and checkpoint cases rejected | Pass |
| Registration and free RAM are prerequisites | Missing confirmation and one KiB below six GiB prevent admission and Start-VM | Pass |
| Admission persists before the first possible VM write | Mock Start-VM independently reads the synthetic on-disk record and requires persisted started/registration fields | Pass |
| Persistence/start failures do not create false readiness or automatic retries | Failed save prevents start; failed start retains one-shot admission, emits no success output and rejects retry | Pass |

## Boundaries and remaining evidence

The tests parse the production coordinator with PowerShell's AST, extract its functions, and execute its unchanged coordinator body after removing only its two imports. Hyper-V and host queries are explicit mocks. Real file hashing and ownership persistence operate only on harmless synthetic bytes in a newly created private test directory, which is removed after an absolute-path/reparse-point cleanup check.

Successful live Attach was deliberately not executed: VM SID translation, VM-specific file ACL access and Hyper-V DVD/firmware operations still require controlled integration verification. Existing imported identity/path and preparation-manifest validators are dependency boundaries; this suite checks that coordinator admission calls them and does not duplicate the separately reviewed preparation suite.

No real VM, VHD, ISO, bootstrap package, credential, installer, registry or host security policy was accessed or changed. No claim is made that the guest is installed, Defender is functioning in the guest, or installer/playback/UAC acceptance has passed.

## Follow-up: Hyper-V worker capability ACL regression

Root reported a later live attachment postflight failure: Hyper-V added its worker capability ACE to `answer.iso`, and the prior validator rejected that additional identity. This follow-up used only new synthetic private files and actual Windows DACLs; it did not inspect the real private answer package, a VM token, or guest credentials.

The final approved contract accepts the exact fixed capability SID only on a file named `answer.iso`, paired with the supplied exact VM SID. Both grants must be explicit, noninherited Allow ACEs with exactly `Read | Synchronize` and no inheritance or propagation flags. With the capability present, extra VM Execute rights are rejected too. The older VM ReadAndExecute allowance is retained only when the capability is absent. Credentials, manifests, directories, unrelated media and unknown capabilities receive no exception.

**RED evidence:** Against preparation SHA-256 `4C1074B417F00F1F33201ED26C8CD2BE90AF889748674FCA8A7DD50C964C999D`, the synthetic observed VM-plus-capability ACL reproduced the rejection. After adjusting the test positive case to the approved stricter Read/Synchronize pair, the suite exited **1**, with **21 passes and one failure**: the intended positive pair was still rejected. An initial fixture-writing issue affected three directory tests because PowerShell `Set-Acl` attempted unrelated security sections; the test helper was corrected to persist only its own DACL with .NET access-control APIs. All directory and inheritance cases then executed without elevation.

**GREEN evidence:** After the production fix, fresh execution returned:

| Command | Result |
| --- | --- |
| `& ./packaging/acceptance-acl.test.ps1` | **26 passed, zero failed, exit 0** |
| `& ./packaging/prepare-acceptance-unattend.test.ps1` | **33 passed, exit 0** |
| `& ./packaging/acceptance-bootstrap.test.ps1` | **39 passed, zero failed, exit 0** |

The ACL suite covers the valid pair, prior VM-only compatibility, missing/wrong/unprovided VM identity, capability-only grants, unknown capability, forbidden credential/manifest/directory/other-ISO targets, additional Write/Execute/Delete/owner/permission rights, real inherited capability and VM grants, mixed valid explicit plus inherited VM grants, incomplete masks, deny-only pairing and excessive VM rights. The last four cases were added after the first GREEN, followed by a complete rerun of the current ACL suite. The current negative fixtures use valid Read/Synchronize VM grants so a VM-mask failure cannot conceal an unrelated capability-validation failure.

Verified production hashes for this follow-up:

- `packaging/prepare-acceptance-unattend.ps1`: `26B28BC13B948E93E517FC7AA3D585BC6599ABA7155CFF5EA9EBB3CF45711395`.
- `packaging/acceptance-bootstrap.ps1`: `E7CE58BD693522BEA2E56B7399EDEEE469E99E08ED5C2A9314B2A24E056DD37A`.
- `packaging/hyperv-acceptance.ps1`: `30C29ADF6B0CB4D50D04DDDD7AFA92184BCC4F00EAA6314A6B4D6988CEB447F4`.

**Follow-up verdict: pass for the bounded ACL fix and coordinator regression checks.** The preparation rerun generated only disposable synthetic secrets/answer media and did not mount or use a real ISO. No live VM, production credential, real bootstrap package or host policy was accessed or changed by this reviewer. These results do not establish live worker-token access semantics or a successful guest installation; controlled host/guest integration remains a separate acceptance gate.
