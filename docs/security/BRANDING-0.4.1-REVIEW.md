# Independent 0.4.1 branding and packaging review

Date: 2026-09-06. Reviewer: independent frontend worker reviewing root-owned Electron/helper/packaging changes. No reviewed production file was edited by this review.

## Verdict

No material production defect found in the reviewed rename. Source compatibility, native helper discovery, production archive contents, staged layouts, and targeted regression checks pass. An installed 0.4.0-to-0.4.1 upgrade and a successful real UAC/Defender exchange are **not verified** by this report.

No outstanding finding. An adjacent tooling observation was resolved by scope clarification: `packaging/verify-management-release.py` remains explicitly tied to 0.4.0. Lines 17 and 31 require version `0.4.0` and the previous helper filenames; its final artifact list also names 0.4.0 installers. The packaging owner confirmed it is intentionally retained as historical 0.4.0 evidence and assigned a separate `verify-release.py` for the new release. This reviewer does not count the historical script as a 0.4.1 gate and has not reviewed the new verifier. The independent unpacked-artifact checks below cover the renamed helper and archive contents directly.

## Baseline and scope

Baseline: `../implementation-evidence/branding-sandbox-baseline-20260906-155330.zip`.

SHA-256: `fad5fa44123d5d342b2c4fc8bbe8237cf82399f2ea37c23f6e1ca8e9a8db0a75`.

Reviewed `electron/main.cjs`, the scanner project assembly identity and test launch paths, `packaging/products.cjs`, `stage.py`, `smoke-install.ps1`, root/frontend package manifests and locks, and `backend/application.py` version. Adjacent consumers inspected: the broker, native exchange, NSIS configuration/templates, runtime manifests and current unpacked application archives. Source comparisons normalize line endings; package locks were compared structurally.

| Requirement / risk | Evidence | Result |
| --- | --- | --- |
| Renamed helper remains discoverable | Project assembly name, development fallback, packaged path, stage input/output, and test launch path all resolve `RedLotus.ScanHelper.exe`; existing native tests executed | Pass |
| Assembly rename preserves scanner protocol | `Program.cs`, `Protocol.cs`, `NativeExchange.cs`, `SecurePipe.cs`, `ManagedFile.cs`, `TrustedProcess.cs`, and `DefenderScanner.cs` unchanged against baseline; native PID/file/nonce tests passed | Pass |
| Runtime protection remains in published executable | Actual renamed single-file binary rejected an injected harmless startup hook and invalid request | Pass |
| Installer identities remain distinct and stable | Both `appId` values unchanged; installed electron-builder derives NSIS GUID from app ID; package identity tests passed | Pass at source/config layer |
| Existing library/history/session data paths retained | Same explicit `LOCALAPPDATA` product directories, `nightops.sqlite`, session partition/cookie keys and backend data environment; only log filename changes to `redlotus.log` | Pass at source layer; installed upgrade not exercised |
| No previous helper bundled | Both new staging inventories and both current unpacked resource directories contain exactly one helper, `RedLotus.ScanHelper.exe`, matching the tested published binary hash | Pass |
| Player/Bridge production boundary unchanged | Layout checks passed; only Bridge has Java/device modules; both ASARs contain exactly four production Electron modules plus package manifest with source parity | Pass |
| Version and dependencies consistent | Root/frontend versions and backend version are 0.4.1; lock changes affect only root package entries, no dependency entries; runtime/wheel locks unchanged | Pass |

The internal `NightOps.ScannerHelper` namespace and `NightOps.Scanner.*` pipe prefix are intentionally unchanged. Native exchange starts the same executable through `Environment.ProcessPath`; it does not assume the former assembly basename. The rename does not change request serialization or OS process identity checks.

Stable IDs are `com.redlotus.nightops.bridge` and `com.redlotus.nightops.player`. With the installed electron-builder 26.15.3 namespace UUID, their NSIS GUIDs remain `d15c0f6b-3214-5195-809e-d019f50465bd` and `991f65fb-2ae3-5a67-bee5-cec3370d3040`. Its templates recover the previous install location and shortcut names from the same registry identity and invoke the previous uninstaller with `/KEEP_APP_DATA`. `deleteAppDataOnUninstall: false`, per-user installation, and `requestedExecutionLevel: asInvoker` are unchanged. This is source evidence, not an executed migration claim.

The old helper still exists in the historical publish directory alongside the new binary. Staging copies a single exact input into a newly reset product directory; it does not copy the publish directory wholesale. Direct inspections confirmed the historical executable is absent from both current products.

## Fresh commands and results

All commands ran from `C:/Users/rober/Desktop/Projects/walkman/walkman-bridge` unless indicated. No frontend build, helper rebuild/publish, stage mutation, installer execution, VM access, installed user product access, or physical Sony access occurred.

```powershell
node --test electron/*.test.cjs packaging/*.test.cjs
# Exit 0: 28 passed, 0 failed. Includes boundary, lifecycle, broker, and product identity tests.

& './backend/.venv/Scripts/python.exe' -B -m unittest discover -s packaging -p test_stage.py -v
# Exit 0: 4 passed (archive escape, dependency hash, cleanup boundary, wheel layout).

& './scanner-helper/artifacts/build/bin/ScannerHelper.Tests/release_win-x64/ScannerHelper.Tests.exe'
# Exit 0: ScannerHelper.Tests: 9 passed.
```

The Python sandbox launcher initially failed before executing source comparison (`Unable to create process using ... uv/python/.../python.exe`). The same read-only comparison and the Python test command were run successfully with permitted escalation. Native helper execution also used escalation; this does not itself request UAC or prove an elevated scan.

Published hardening check, executed from `scanner-helper/tests`. Only the proof output directory was redirected in memory, preserving the owner's existing proof and source script:

```powershell
$reviewHardening = (Get-Content -LiteralPath './Verify-PublishedHardening.ps1' -Raw).Replace('hardening-proof-041', 'hardening-review-041')
$reviewHardening = $reviewHardening.Replace('$PSScriptRoot', "'C:/Users/rober/Desktop/Projects/walkman/walkman-bridge/scanner-helper/tests'")
& ([ScriptBlock]::Create($reviewHardening))
```

Exit 0. Output: `ok=true`, `startup_hook_executed=false`, `invalid_request_exit_code=1`, stderr `scanner_exchange_failed`. Independent proof: `scanner-helper/artifacts/hardening-review-041/published-startup-hook.json`.

Tested published helper SHA-256, also matched by both staged and unpacked copies:

`28123ee65e1885f04f4a45a07930274be5bdcb7b703f78b4686e93910ed926a2`.

Installer smoke syntax was parsed without executing the script:

```powershell
$reviewParseErrors = $null
$reviewParseTokens = $null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path packaging/smoke-install.ps1), [ref]$reviewParseTokens, [ref]$reviewParseErrors) | Out-Null
if ($reviewParseErrors.Count) { $reviewParseErrors | Format-List; exit 1 }
```

Exit 0. Its registration guard recognizes both the new display name and the former Bridge display name; isolated app-data environment variables and diagnostic log lookup agree with the changed launcher. No registry access or installer action was performed by this parse.

Additional read-only checks ran through Python `-B -c` to compare baseline zip entries, import `packaging/stage.py`, execute `validate_layout` against `packaging/build/redlotus/{bridge,player}`, and assert both inventories contain only the new helper and version 0.4.1. Both products passed. `electron/boundary.cjs`, `scanner-broker.cjs`, `preload.cjs`, helper protocol/security implementations, `electron-builder.yml`, runtime lock and wheel lock were identical after line-ending normalization.

The following exact JavaScript body was executed with `node -e` for the current unpacked products (read-only):

```javascript
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),asar=require('@electron/asar');
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const expected=['electron/boundary.cjs','electron/main.cjs','electron/preload.cjs','electron/scanner-broker.cjs','package.json'].sort();
for(const product of ['bridge','player']){
 const root=path.resolve('dist_electron',product,'win-unpacked'),resources=path.join(root,'resources'),archive=path.join(resources,'app.asar');
 const manifest=JSON.parse(fs.readFileSync(path.join(resources,'runtime-manifest.json')));
 assert.equal(manifest.version,'0.4.1');
 assert.equal(manifest.product,product);
 const helperFiles=fs.readdirSync(path.join(resources,'scanner-helper'));
 assert.deepEqual(helperFiles,['RedLotus.ScanHelper.exe']);
 assert.equal(hash(fs.readFileSync(path.join(resources,'scanner-helper',helperFiles[0]))),hash(fs.readFileSync('scanner-helper/artifacts/win-x64/RedLotus.ScanHelper.exe')));
 const entries=asar.listPackage(archive).map(n=>n.replaceAll(String.fromCharCode(92),'/').replace(/^\//,'')).filter(n=>!asar.statFile(archive,n).files).sort();
 assert.deepEqual(entries,expected);
 for(const entry of expected.filter(n=>n.startsWith('electron/')))assert.equal(hash(asar.extractFile(archive,entry)),hash(fs.readFileSync(entry)));
 const packaged=JSON.parse(asar.extractFile(archive,'package.json'));
 assert.equal(packaged.version,'0.4.1');
 assert.equal(packaged.productName,product==='bridge'?'Walkman Bridge':'Red Lotus Player');
 const exes=fs.readdirSync(root).filter(n=>n.endsWith('.exe'));
 assert.equal(exes.some(n=>/night.?ops/i.test(n)),false);
 console.log(JSON.stringify({product,version:manifest.version,helperFiles,asarEntries:entries,executables:exes,passed:true}));
}
```

Exit 0. Bridge executable list: `["Walkman Bridge.exe"]`; Player: `["Red Lotus Player.exe"]`. Both emitted `passed:true`, version 0.4.1, one new helper and the expected five ASAR entries.

## Reviewed source fingerprints

SHA-256 values at review completion:

```text
7526c5e5f303edb275a0983322ae71b7340a1ebe2271d15274466f28a269f719  electron/main.cjs
8c5a97874323a6ef935c4cbd205656d820f61776f5fdd960ead864d89593ae67  scanner-helper/ScannerHelper/ScannerHelper.csproj
338abfeb09d83429d2a1e9387828fdc7c3962413027bbe13cb5575b879390b37  scanner-helper/ScannerHelper.Tests/Program.cs
d01a2d297523b74c4f3a4c2c69f85467e839582a7706bfef868c1a4d40e7e5fa  packaging/products.cjs
884111a4dede4af6a43991bcbafb3279a5670aeaedf38667a20776658c6d1129  packaging/stage.py
2d6af586db659fa25d911669b63bc626a3cc9d6750eb1f7b5bb1fe6d07dc158d  packaging/smoke-install.ps1
1b559181dbaf43b4e8aeb48ee9c066eed201c23e580b8f9e1b609eb4921b07f9  package.json
52da79bf15ce1b20c42abf4021f1f566a94f10a024751553eb027bc9394943b7  package-lock.json
5a89a62cbf07628de11e8352a642caa44525bccb0b37040cf60c8b0b0934843b  frontend/package.json
2e61f243f8721302333d74dfe1624ea05f6b7e40781e4f49a6d2b2eee774b203  frontend/package-lock.json
6d85dc0ea07bfc55c6bb092e1a7c37c8f2e88fe88cdc8b85103fc1657c4eb326  backend/application.py
```

## Evidence limits

The coordinator reported a prior opt-in real UAC attempt returned `elevation_cancelled`, with zero claims/results. That is not a successful scan, and this reviewer did not repeat it. The native one-shot test accepts a correctly bound fail-closed Defender outcome; its success proves exchange validation, not that Defender granted clean clearance. No installed upgrade, uninstall, VM, physical firmware behavior, installer execution, or code-signing assertion is made here. Final installer/archive publication remains the coordinator's responsibility.

## Supplemental harness review — 2026-09-06

Reviewed the new `hyperv-acceptance.ps1`, `hyperv-acceptance-guest.ps1`, and `test-upgrade.ps1`; also reviewed the owner's corresponding cleanup corrections in `smoke-install.ps1`. **No outstanding material finding remains in the reviewed revisions.** This supplement is source review and mocked fault-injection evidence, not a VM or installer acceptance run.

The review identified and the owners corrected these issues:

| Finding | Original consequence | Independent fix verification |
| --- | --- | --- |
| VM disk attachment was bounded only to the broad `hyperv` directory | A sibling VM's disk could pass ownership validation. The original guard was reproduced accepting a sibling path with fake metadata only. | New guard requires the fixed base disk and an entirely owned, bounded, non-cyclic checkpoint chain terminating at that base. Sibling disk, wrong recorded base, external parent, cyclic parent, reparse ancestor, and unverified checkpoint were independently rejected. |
| VM Start trusted the previous media hash record | Replaced ISO bytes or a changed DVD attachment could be started without current verification. | Start now checks exactly one DVD, the recorded scoped ISO path, and its current official hash. Changed bytes, changed path, and extra DVD were independently rejected. |
| Upgrade/install proof was published before cleanup; cleanup failure could skip environment restoration | A success JSON could survive an unsuccessful uninstall, and redirected environment values could remain in the harness process. | Both scripts now require body success **and** verified cleanup before `passed:true`, check registration/shortcut removal, and restore environment inside a nested final block. Injected uninstall failures emitted `passed:false`, `cleanup.passed:false`, and restored every affected environment variable. |
| Registry enumeration errors were suppressed | Failed inventory could be mistaken for no existing installation or successful registration removal. | Both registration functions now distinguish an absent root from a read failure using `-ErrorAction Stop`. Injected enumeration denial propagated in both guards. |

The host VM harness refuses to adopt an existing named VM or target directory, binds subsequent actions to the recorded VM GUID/name/configuration, checks resource limits and the fixed network/firmware contract, and performs no automatic VM deletion or shutdown of other VMs. Checkpoint creation requires positive guest evidence and refuses to replace an existing named checkpoint. Credentials are supplied through `PSCredential` and are not written into the ownership/evidence records. The guest script is read-only and requires affirmative Windows edition, Defender, firmware, tool-inventory, application-absence and reboot checks; a non-VM fixture returned `passed:false` with an explicit error.

The upgrade fixture uses the old installed runtime to create a real managed file, playlist, playback settings and terminal job, then reads them with the new installed runtime. Its only intentional expected-data adjustment is `queued` to `needs_scan`, matching `MediaStore` reopen recovery at `backend/media_store.py:125`. Other persisted fields and original/cache bytes must remain equal; no synthetic scan clearance is supplied. Existing current/legacy product registrations, running products, and desktop/start-menu shortcuts are checked before installation. Cleanup only invokes the uninstaller beneath the unique test install directory and leaves timed-out running processes for diagnosis while recording failure.

Fresh supplemental checks (all exit 0):

- PowerShell `Parser.ParseFile` syntax checks for all four harnesses; no script entry point executed.
- AST-extracted Hyper-V path/disk/media functions with mocked filesystem and Hyper-V metadata: **12/12** expected results, including valid base, verified checkpoint and current media acceptance, plus the nine rejection cases above. No Hyper-V module import or cmdlet execution occurred.
- AST-extracted final blocks for both installation harnesses, with uninstall process calls replaced by throwing mocks and `Set-Content` replaced by an in-memory capture: both preserved false failure evidence and restored environment values.
- AST-extracted `Registrations` functions with an injected registry enumeration exception: both propagated the exception instead of returning an empty inventory. No host registry was read for these checks.
- Guest script invoked with its initial `Get-CimInstance` replaced by a non-VM fixture: correctly rejected before any real inventory query.

Final reviewed SHA-256 values (the smoke harness value supersedes its earlier fingerprint in this report):

```text
8443036c8a7b17dde0cd31b2e68b7b6fe90341e69869bfab98ed5ed10f1169d7  packaging/hyperv-acceptance.ps1
72419dfb2fe129fb979e3bc2001ea80d7dc9a5fe8c8d35e3ddc313f29f95aa7c  packaging/hyperv-acceptance-guest.ps1
ba27ee8ec1bdc7ceb1ddf8d197ef957981a581d587e6950f0591085cc3cf9ae9  packaging/test-upgrade.ps1
21b405bf5391ee0e128f25527465aa1f5ec4279817d083d726c7d103995d2d07  packaging/smoke-install.ps1
```

No production/dist edits, VM actions, installer/uninstaller execution, host registration changes, shortcut mutations, or physical device access were performed by this supplemental review. Actual corrected upgrade/fresh-install runs remain the coordinator's separate evidence.
