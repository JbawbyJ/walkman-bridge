#requires -Version 7.2
<# Independent coordinator tests. Hyper-V commands are mocked and only synthetic
   files below one newly created private test directory are read or written.
   No VM, ISO mount, installer, registry, policy or real bootstrap package is used. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$BootstrapSource = Join-Path $PSScriptRoot 'acceptance-bootstrap.ps1'
$SourceText = Get-Content -LiteralPath $BootstrapSource -Raw
$Tokens = $null; $ParseErrors = $null
$SourceAst = [Management.Automation.Language.Parser]::ParseInput($SourceText,[ref]$Tokens,[ref]$ParseErrors)
if ($ParseErrors.Count) { throw 'Bootstrap source does not parse.' }
$Results = [Collections.Generic.List[object]]::new()
function Assert-True([bool]$Value,[string]$Message) { if (-not $Value) { throw $Message } }
function Test-Case([string]$Name,[scriptblock]$Body) {
    try { & $Body; $script:Results.Add([pscustomobject]@{name=$Name;passed=$true}) }
    catch { $script:Results.Add([pscustomobject]@{name=$Name;passed=$false;error=$_.Exception.Message}) }
}
function Assert-Throws([scriptblock]$Body,[string]$Expected) {
    $Failure = $null
    try { & $Body | Out-Null } catch { $Failure = $_.Exception.Message }
    Assert-True ($null -ne $Failure) 'Expected rejection, but operation succeeded.'
    if ($Expected) { Assert-True ($Failure.Contains($Expected)) ('Unexpected rejection: ' + $Failure) }
}

# Use the real import path and entrypoint here. Any actual environment action is
# intercepted before it can reach the host. Describe/import require no VM module.
$script:UnexpectedActions = [Collections.Generic.List[string]]::new()
function Import-Module { param($Name) $script:UnexpectedActions.Add("Import-Module $Name"); throw 'Unexpected module import.' }
function Get-VM { $script:UnexpectedActions.Add('Get-VM'); throw 'Unexpected VM access.' }
function Get-CimInstance { $script:UnexpectedActions.Add('Get-CimInstance'); throw 'Unexpected host query.' }
function Start-VM { $script:UnexpectedActions.Add('Start-VM'); throw 'Unexpected VM start.' }
Test-Case 'Describe uses real imports without environment actions' {
    $Description = & $BootstrapSource -BootstrapAction Describe | ConvertFrom-Json
    Assert-True ($Description.default_mutations -eq $false -and $Description.registration_required_for_start) 'Describe contract changed.'
    Assert-True ($script:UnexpectedActions.Count -eq 0) 'Describe called an environment command.'
}
foreach ($Mode in @('Attach','Start')) {
    Test-Case "Dot-source preserves $Mode and supplied package without environment actions" {
        & {
            $ExpectedPackage = 'C:\synthetic-bootstrap-parameter-sentinel'
            . $BootstrapSource -BootstrapAction $Mode -PackagePath $ExpectedPackage
            Assert-True ($PackagePath -eq $ExpectedPackage) 'An imported script clobbered PackagePath.'
            Assert-True ($BootstrapAction -eq $Mode) 'An imported script clobbered BootstrapAction.'
            Assert-True ($script:UnexpectedActions.Count -eq 0) 'Dot-source called an environment command.'
        }
    }
}
Test-Case 'Hyper-V helper dot-source guard precedes module import' {
    & {
        $Output = @(. (Join-Path $PSScriptRoot 'hyperv-acceptance.ps1') -Action Start)
        Assert-True ($Output.Count -eq 0 -and $script:UnexpectedActions.Count -eq 0) 'Hyper-V helper import had runtime effects.'
    }
}
Test-Case 'Regression control reproduces package clobber without save/restore fix' {
    & {
        $FirstFunction=$SourceAst.Find({param($Node) $Node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)
        $LegacyImports=$SourceText.Substring(0,$FirstFunction.Extent.StartOffset).
            Replace('$RequestedBootstrapPackagePath = $PackagePath','').
            Replace('$PackagePath = $RequestedBootstrapPackagePath','').
            Replace('$PSScriptRoot',("'"+$PSScriptRoot.Replace("'","''")+"'"))
        . ([scriptblock]::Create($LegacyImports)) -BootstrapAction Attach -PackagePath 'C:\synthetic-bootstrap-parameter-sentinel'
        Assert-True ($PackagePath -ne 'C:\synthetic-bootstrap-parameter-sentinel') 'Regression control no longer reproduces original import defect.'
        Assert-True ($script:UnexpectedActions.Count -eq 0) 'Regression control called an environment command.'
    }
}

# Extract the production function definitions through the AST, and keep the
# production coordinator body intact apart from removing its two imports. The
# imported implementation dependencies are replaced only with declared mocks.
$RuntimeText = $SourceText
$Imports = @($SourceAst.FindAll({param($Node) $Node -is [Management.Automation.Language.CommandAst] -and $Node.InvocationOperator -eq 'Dot'},$true))
Assert-True ($Imports.Count -eq 2) 'Review required: bootstrap import surface changed.'
foreach ($Import in ($Imports | Sort-Object {$_.Extent.StartOffset} -Descending)) {
    $RuntimeText = $RuntimeText.Remove($Import.Extent.StartOffset,$Import.Extent.EndOffset-$Import.Extent.StartOffset)
}
$Runtime = [scriptblock]::Create($RuntimeText)
foreach ($Definition in $SourceAst.FindAll({param($Node) $Node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
    . ([scriptblock]::Create($Definition.Extent.Text))
}

# Reuse only the atomic private-directory helper, extracted without importing or
# executing preparation. No credentials or answer-file generation occurs here.
$PrepErrors=$null; $PrepTokens=$null
$PrepAst=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'prepare-acceptance-unattend.ps1'),[ref]$PrepTokens,[ref]$PrepErrors)
if ($PrepErrors.Count) { throw 'Preparation helper does not parse.' }
foreach ($Name in @('Assert-AcceptanceChildPath','Assert-AcceptancePrivateAcl','New-AcceptancePrivateDirectory')) {
    $Definition=$PrepAst.Find({param($Node) $Node -is [Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -eq $Name},$false)
    . ([scriptblock]::Create($Definition.Extent.Text))
}
$TestRoot=Join-Path (Join-Path $PSScriptRoot 'build') ('bootstrap-review-'+[Guid]::NewGuid().ToString('N'))
New-AcceptancePrivateDirectory $TestRoot | Out-Null
$ManifestPath=Join-Path $TestRoot 'ownership.json'
$VmName='Red-Lotus-Windows-Acceptance'
$script:Fixture=$null
function Reset-Fixture {
    $Package=Join-Path $TestRoot 'synthetic-package'
    if (-not (Test-Path -LiteralPath $Package)) { New-AcceptancePrivateDirectory $Package | Out-Null }
    $DiskPath=Join-Path $TestRoot 'synthetic-disk.vhdx'
    $Installer=Join-Path $TestRoot 'synthetic-install.iso'
    $Answer=Join-Path $Package 'answer.iso'
    [IO.File]::WriteAllText($DiskPath,'synthetic bytes; this is not a virtual disk')
    [IO.File]::WriteAllText($Installer,'synthetic bytes; this is not installation media')
    [IO.File]::WriteAllText($Answer,'synthetic answer bytes; no credentials')
    [IO.File]::WriteAllText((Join-Path $Package 'manifest.json'),'synthetic preparation manifest')
    $script:OfficialHash=(Get-FileHash -LiteralPath $Installer).Hash
    $Id=[Guid]'11111111-2222-3333-4444-555555555555'
    $Owner=[pscustomobject]@{state='bootstrap_attached';clean_checkpoint_verified=$false;disk_path=$DiskPath;bootstrap=[pscustomobject]@{
        package_path=$Package;manifest_sha256=(Get-FileHash -LiteralPath (Join-Path $Package 'manifest.json')).Hash;
        answer_sha256=(Get-FileHash -LiteralPath $Answer).Hash;disk_sha256=(Get-FileHash -LiteralPath $DiskPath).Hash;
        disk_identifier='synthetic-disk-identifier';installation_started=$false;guest_cleanup_verified=$false}}
    $Owner | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ManifestPath -Encoding utf8
    $script:Fixture=[pscustomobject]@{
        Package=$Package;Machine=[pscustomobject]@{Id=$Id;State='Off'};Owner=$Owner;
        Preparation=[pscustomobject]@{vm_id=$Id.ToString();guest_disk_id=0;guest_disk_bytes=64GB;package_path=$Package;installation_iso_path=$Installer;files=@{'answer.iso'=(Get-FileHash -LiteralPath $Answer).Hash}};
        Disks=@([pscustomobject]@{ControllerNumber=0;ControllerLocation=0;ControllerType='SCSI';Path=$DiskPath});
        Vhd=[pscustomobject]@{Size=64GB;FileSize=4MB;Attached=$false;VhdType='Dynamic';ParentPath='';DiskIdentifier='synthetic-disk-identifier'};
        Checkpoints=@();Dvds=@([pscustomobject]@{Path=$Installer},[pscustomobject]@{Path=$Answer});
        FreeMemoryKB=8GB/1KB;Events=[Collections.Generic.List[string]]::new();CapturedPackage='';PreparationSentinel=$false;
        SaveFails=$false;StartFails=$false;ConfigurationFails=$false;Persisted=$null;StartCalls=0
    }
}
function Import-Module { param($Name) if ($Name -ne 'Hyper-V') { throw 'Unexpected module.' }; $script:Fixture.Events.Add('module_mock') }
function Get-OwnedVm { return $script:Fixture.Machine }
function Get-AcceptancePreparationManifest {
    param($Path)
    $script:Fixture.CapturedPackage=$Path
    if ($script:Fixture.PreparationSentinel) { throw 'package-captured-sentinel' }
    if ($Path -ne $script:Fixture.Package) { throw 'Wrong package parameter.' }
    return $script:Fixture.Preparation
}
function Assert-VmConfiguration { param($Machine) if ($script:Fixture.ConfigurationFails) { throw 'VM configuration changed.' }; $script:Fixture.Events.Add('configuration_checked') }
function Get-VMCheckpoint { param($VM) return $script:Fixture.Checkpoints }
function Get-VMHardDiskDrive { param($VM) return $script:Fixture.Disks }
function Assert-OwnedDiskPath { param($Path) if ($Path -ne $script:Fixture.Owner.disk_path) { throw 'Disk path escaped.' }; return $Path }
function Get-VHD { param($Path) return $script:Fixture.Vhd }
function Assert-ScopedMediaPath { param($Path) if ($Path -ne (Join-Path $TestRoot 'synthetic-install.iso')) { throw 'Media path escaped.' }; return $Path }
function Get-VMDvdDrive { param($VM) return $script:Fixture.Dvds }
function Get-CimInstance { param($ClassName) if ($ClassName -ne 'Win32_OperatingSystem') { throw 'Unexpected host query.' }; return [pscustomobject]@{FreePhysicalMemory=$script:Fixture.FreeMemoryKB} }
function Save-Ownership {
    param($Value)
    $script:Fixture.Events.Add('save_attempt')
    if ($script:Fixture.SaveFails) { throw 'synthetic ownership persistence failure' }
    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ManifestPath -Encoding utf8
    $script:Fixture.Persisted=Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $script:Fixture.Events.Add('saved')
}
function Start-VM {
    param($VM)
    $script:Fixture.StartCalls++
    $script:Fixture.Events.Add('start_attempt')
    $OnDisk=Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ($OnDisk.state -ne 'bootstrap_started' -or -not $OnDisk.bootstrap.installation_started -or -not $OnDisk.bootstrap.registration_confirmed) { throw 'Start called before persisted admission.' }
    if ($script:Fixture.StartFails) { throw 'synthetic uncertain start failure' }
}
function Add-VMDvdDrive { throw 'Test must never attach real or mocked media.' }
function Set-VMDvdDrive { throw 'Test must never change real or mocked media.' }
function Set-VMFirmware { throw 'Test must never change firmware.' }
function Invoke-Start { & $Runtime -BootstrapAction Start -PackagePath $script:Fixture.Package -EvaluationRegistrationConfirmed }
function Assert-StartRejected([scriptblock]$Change,[string]$Expected) {
    Reset-Fixture
    & $Change
    Assert-Throws { Invoke-Start } $Expected
    Assert-True ($script:Fixture.StartCalls -eq 0 -and $null -eq $script:Fixture.Persisted) 'Rejected input reached admission/start.'
}

try {
    foreach ($Mode in @('Attach','Start')) {
        Test-Case "$Mode forwards exact package into verified manifest admission" {
            Reset-Fixture; $script:Fixture.PreparationSentinel=$true
            Assert-Throws { & $Runtime -BootstrapAction $Mode -PackagePath $script:Fixture.Package -EvaluationRegistrationConfirmed } 'package-captured-sentinel'
            Assert-True ($script:Fixture.CapturedPackage -eq $script:Fixture.Package -and $script:Fixture.StartCalls -eq 0) 'Supplied package was not preserved.'
        }
    }
    Test-Case 'Attach refuses an already recorded attempt' {
        Reset-Fixture
        Assert-Throws { & $Runtime -BootstrapAction Attach -PackagePath $script:Fixture.Package } 'bootstrap attempt is already recorded'
        Assert-True ($script:Fixture.StartCalls -eq 0 -and $null -eq $script:Fixture.Persisted) 'Attach replacement admitted work.'
    }
    foreach ($DvdCase in @('extra','unexpected')) {
        Test-Case "Attach refuses $DvdCase DVD before mutations" {
            Reset-Fixture
            $script:Fixture.Owner.state='created_off_without_os'
            $script:Fixture.Owner.PSObject.Properties.Remove('bootstrap')
            $script:Fixture.Owner | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ManifestPath -Encoding utf8
            if ($DvdCase -eq 'unexpected') { $script:Fixture.Dvds=@([pscustomobject]@{Path='unowned.iso'}) }
            Assert-Throws { & $Runtime -BootstrapAction Attach -PackagePath $script:Fixture.Package } 'unexpected DVD is attached'
            Assert-True ($script:Fixture.StartCalls -eq 0 -and $null -eq $script:Fixture.Persisted) 'Unexpected DVD admitted work.'
        }
    }
    Test-Case 'Valid start persists admission before starting and does not claim guest readiness' {
        Reset-Fixture
        $Result=Invoke-Start | ConvertFrom-Json
        Assert-True ($script:Fixture.StartCalls -eq 1 -and $Result.state -eq 'bootstrap_started' -and $Result.guest_ready -eq $false) 'Wrong start/result contract.'
        Assert-True ($script:Fixture.Events.IndexOf('saved') -lt $script:Fixture.Events.IndexOf('start_attempt')) 'Admission was not saved before start.'
        Assert-True ($script:Fixture.Events.Contains('configuration_checked')) 'VM configuration gate was skipped.'
        Assert-Throws { Invoke-Start } 'Only the owned first-installation'
        Assert-True ($script:Fixture.StartCalls -eq 1) 'Start was automatically repeated.'
    }
    Test-Case 'Missing registration blocks start' {
        Reset-Fixture
        Assert-Throws { & $Runtime -BootstrapAction Start -PackagePath $script:Fixture.Package } 'registration prerequisite'
        Assert-True ($script:Fixture.StartCalls -eq 0 -and $null -eq $script:Fixture.Persisted) 'Missing registration admitted work.'
    }
    Test-Case 'Insufficient host RAM blocks admission' { Assert-StartRejected { $script:Fixture.FreeMemoryKB=(6GB-1KB)/1KB } '6 GiB free host RAM' }
    Test-Case 'Different live VM identity blocks start' { Assert-StartRejected { $script:Fixture.Preparation.vm_id=[Guid]::NewGuid().ToString() } 'identities differ' }
    Test-Case 'Different guest disk index blocks start' { Assert-StartRejected { $script:Fixture.Preparation.guest_disk_id=1 } 'different VM or disk' }
    Test-Case 'Different guest disk capacity blocks start' { Assert-StartRejected { $script:Fixture.Preparation.guest_disk_bytes=32GB } 'different VM or disk' }
    Test-Case 'Changed VM configuration blocks start' { Assert-StartRejected { $script:Fixture.ConfigurationFails=$true } 'VM configuration changed' }
    Test-Case 'Running VM blocks start' { Assert-StartRejected { $script:Fixture.Machine.State='Running' } 'powered off' }
    Test-Case 'Checkpoint blocks first installation' { Assert-StartRejected { $script:Fixture.Checkpoints=@([pscustomobject]@{Id='checkpoint'}) } 'Existing checkpoints' }
    foreach ($Change in @(
        @{name='additional hard disk';code={ $script:Fixture.Disks+=($script:Fixture.Disks[0] | Select-Object *) }},
        @{name='different controller';code={ $script:Fixture.Disks[0].ControllerType='IDE' }},
        @{name='different controller number';code={ $script:Fixture.Disks[0].ControllerNumber=1 }},
        @{name='different disk location';code={ $script:Fixture.Disks[0].ControllerLocation=1 }},
        @{name='replacement disk path';code={ $script:Fixture.Disks[0].Path=Join-Path $TestRoot 'other.vhdx' }}
    )) { Test-Case ($Change.name+' blocks start') { Assert-StartRejected $Change.code 'SCSI 0:0' } }
    foreach ($Change in @(
        @{name='different virtual size';code={ $script:Fixture.Vhd.Size=32GB }},
        @{name='expanded disk file';code={ $script:Fixture.Vhd.FileSize=6MB }},
        @{name='already mounted disk';code={ $script:Fixture.Vhd.Attached=$true }},
        @{name='fixed disk';code={ $script:Fixture.Vhd.VhdType='Fixed' }},
        @{name='disk parent chain';code={ $script:Fixture.Vhd.ParentPath='parent.vhdx' }}
    )) { Test-Case ($Change.name+' blocks start') { Assert-StartRejected $Change.code 'fresh, unmounted' } }
    Test-Case 'Changed disk bytes block start' { Assert-StartRejected { [IO.File]::AppendAllText($script:Fixture.Owner.disk_path,'changed') } 'initial disk changed' }
    Test-Case 'Changed disk identifier blocks start' { Assert-StartRejected { $script:Fixture.Vhd.DiskIdentifier='replacement-id' } 'initial disk changed' }
    Test-Case 'Changed installation media bytes block start' { Assert-StartRejected { [IO.File]::AppendAllText($script:Fixture.Preparation.installation_iso_path,'changed') } 'Official installer media changed' }
    Test-Case 'Extra DVD blocks start' { Assert-StartRejected { $script:Fixture.Dvds+=([pscustomobject]@{Path='other.iso'}) } 'exact installation and private answer DVDs' }
    Test-Case 'Replaced answer DVD blocks start' { Assert-StartRejected { $script:Fixture.Dvds[1].Path='other.iso' } 'exact installation and private answer DVDs' }
    Test-Case 'Replaced installation DVD blocks start' { Assert-StartRejected { $script:Fixture.Dvds[0].Path='other.iso' } 'exact installation and private answer DVDs' }
    Test-Case 'Changed recorded package manifest blocks start' { Assert-StartRejected { [IO.File]::AppendAllText((Join-Path $script:Fixture.Package 'manifest.json'),'changed') } 'Recorded bootstrap package changed' }
    Test-Case 'Changed recorded package path blocks start' { Assert-StartRejected { $script:Fixture.Preparation.package_path=Join-Path $TestRoot 'other-package' } 'exact installation and private answer DVDs' }
    Test-Case 'Failed persistence prevents Start-VM' {
        Reset-Fixture; $script:Fixture.SaveFails=$true
        Assert-Throws { Invoke-Start } 'ownership persistence failure'
        Assert-True ($script:Fixture.StartCalls -eq 0) 'Start occurred after persistence failure.'
    }
    Test-Case 'Start failure retains one-shot admission and cannot report guest-ready' {
        Reset-Fixture; $script:Fixture.StartFails=$true; $Output=[Collections.Generic.List[object]]::new()
        Assert-Throws { Invoke-Start | ForEach-Object { $Output.Add($_) } } 'uncertain start failure'
        Assert-True ($Output.Count -eq 0 -and $script:Fixture.Persisted.state -eq 'bootstrap_started' -and $script:Fixture.Persisted.bootstrap.installation_started) 'Start failure produced success output or lost admission.'
        Assert-Throws { Invoke-Start } 'Only the owned first-installation'
        Assert-True ($script:Fixture.StartCalls -eq 1) 'Uncertain start was retried.'
    }
} finally {
    $AbsoluteTestRoot=[IO.Path]::GetFullPath($TestRoot)
    $ExpectedParent=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'build'))
    if ([IO.Path]::GetDirectoryName($AbsoluteTestRoot) -ne $ExpectedParent -or [IO.Path]::GetFileName($AbsoluteTestRoot) -notmatch '^bootstrap-review-[a-f0-9]{32}$') { throw 'Unsafe synthetic cleanup target.' }
    if ((Get-Item -LiteralPath $AbsoluteTestRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unexpected synthetic root reparse point.' }
    Remove-Item -LiteralPath $AbsoluteTestRoot -Recurse -Force
}
$Report=[ordered]@{passed=@($Results | Where-Object passed).Count;failed=@($Results | Where-Object {-not $_.passed}).Count;tests=$Results;scope='Synthetic files and mocked Hyper-V only; no live VM or acceptance evidence.'}
$Report | ConvertTo-Json -Depth 6
if ($Report.failed) { exit 1 }
