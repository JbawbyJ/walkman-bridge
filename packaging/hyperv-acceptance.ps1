#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Inventory','Create','AttachMedia','Start','VerifyClean','Checkpoint')]
    [string]$Action = 'Inventory',
    [string]$IsoPath,
    [System.Management.Automation.PSCredential]$GuestCredential
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$VmName = 'Red-Lotus-Windows-Acceptance'
$Repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$Workspace = (Resolve-Path -LiteralPath (Join-Path $Repo '..')).Path
$HyperVRoot = [IO.Path]::GetFullPath((Join-Path $Workspace 'hyperv'))
$VmRoot = Join-Path $HyperVRoot $VmName
$ManifestPath = Join-Path $VmRoot 'ownership.json'
$CheckpointName = 'Factory-Windows11-Defender-Clean'
$OfficialHash = 'A61ADEAB895EF5A4DB436E0A7011C92A2FF17BB0357F58B13BBC4062E535E7B9'

function Assert-ScopedPath([string]$Path) {
    $FullPath = [IO.Path]::GetFullPath($Path)
    if (-not $FullPath.StartsWith($HyperVRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside the acceptance workspace: $FullPath"
    }
    for ($Ancestor = $FullPath; $Ancestor; $Ancestor = [IO.Path]::GetDirectoryName($Ancestor)) {
        if (Test-Path -LiteralPath $Ancestor) {
            if ((Get-Item -LiteralPath $Ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse point is not permitted: $Ancestor"
            }
        }
    }
    return $FullPath
}

function Save-Ownership($Value) {
    Assert-ScopedPath $ManifestPath | Out-Null
    $Value | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
}

function Assert-OwnedDiskPath([string]$Path) {
    $FullPath = Assert-ScopedPath $Path
    $DiskRoot = [IO.Path]::GetFullPath((Join-Path $VmRoot 'disks'))
    if (-not $FullPath.StartsWith($DiskRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Disk or checkpoint parent is outside this acceptance VM disk directory.'
    }
    return $FullPath
}

function Assert-OwnedDiskChain($Machine, $Ownership) {
    $Base = Assert-OwnedDiskPath (Join-Path $VmRoot 'disks\Windows11-Acceptance.vhdx')
    if ([IO.Path]::GetFullPath($Ownership.disk_path) -ne $Base) { throw 'Recorded base disk differs from the fixed owned disk.' }
    $Disks = @(Get-VMHardDiskDrive -VM $Machine)
    if ($Disks.Count -ne 1 -or -not $Disks[0].Path) { throw 'Exactly one owned virtual disk is required; physical disks are not permitted.' }
    $Current = Assert-OwnedDiskPath $Disks[0].Path
    $CheckpointVerified = $false
    if ($Current -ne $Base) {
        if (-not $Ownership.clean_checkpoint_verified -or -not $Ownership.PSObject.Properties['checkpoint_id']) {
            throw 'An unrecorded differencing disk is not permitted before a verified clean checkpoint.'
        }
        $RecordedCheckpoint = @(Get-VMCheckpoint -VM $Machine -Name $CheckpointName -ErrorAction Stop)
        if ($RecordedCheckpoint.Count -ne 1 -or $RecordedCheckpoint[0].Id.ToString() -ne $Ownership.checkpoint_id) {
            throw 'The clean checkpoint identity differs from the ownership record.'
        }
        $CheckpointVerified = $true
    }
    $Seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    while ($true) {
        if (-not $Seen.Add($Current) -or $Seen.Count -gt 32) { throw 'Invalid cyclic or excessive checkpoint parent chain.' }
        $Info = Get-VHD -Path $Current -ErrorAction Stop
        if ($Info.Size -ne 64GB) { throw 'Every disk in the acceptance chain must have a 64 GiB virtual capacity.' }
        if ($Current -eq $Base) {
            if ([string]$Info.VhdType -ne 'Dynamic' -or $Info.ParentPath) { throw 'The recorded base must be a standalone dynamic disk.' }
            return
        }
        if (-not $CheckpointVerified -or [string]$Info.VhdType -ne 'Differencing' -or
            [IO.Path]::GetExtension($Current) -ne '.avhdx' -or -not $Info.ParentPath -or
            -not [IO.Path]::IsPathRooted($Info.ParentPath)) { throw 'Unsupported or incomplete checkpoint parent chain.' }
        $Current = Assert-OwnedDiskPath $Info.ParentPath
    }
}

function Assert-ScopedMediaPath([string]$Path) {
    $FullPath = Assert-ScopedPath (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    $MediaRoot = [IO.Path]::GetFullPath((Join-Path $HyperVRoot 'media'))
    if (-not $FullPath.StartsWith($MediaRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetExtension($FullPath) -ne '.iso' -or -not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        throw 'Installation media must be an ISO in the dedicated workspace hyperv/media directory.'
    }
    return $FullPath
}

function Assert-RecordedMedia($Machine, $Ownership) {
    if ($Ownership.media_sha256 -ne $OfficialHash -or -not $Ownership.media_path) { throw 'Attach verified installation media first.' }
    $Dvd = @(Get-VMDvdDrive -VM $Machine)
    if ($Dvd.Count -ne 1 -or -not $Dvd[0].Path) { throw 'Exactly one recorded installation DVD is required before starting.' }
    $Recorded = Assert-ScopedMediaPath $Ownership.media_path
    $Attached = Assert-ScopedMediaPath $Dvd[0].Path
    if ($Attached -ne $Recorded -or (Get-FileHash -LiteralPath $Attached -Algorithm SHA256).Hash -ne $OfficialHash) {
        throw 'Attached media path or current ISO hash differs from the recorded official media.'
    }
}

function Get-OwnedVm {
    Assert-ScopedPath $ManifestPath | Out-Null
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw 'No owned acceptance VM manifest exists.' }
    $Ownership = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ($Ownership.name -ne $VmName -or $Ownership.root -ne $VmRoot -or -not $Ownership.vm_id) { throw 'Acceptance ownership manifest does not match the fixed target.' }
    $Machine = Get-VM -Id ([Guid]$Ownership.vm_id)
    if ($Machine.Name -ne $VmName -or [IO.Path]::GetFullPath($Machine.Path) -ne [IO.Path]::GetFullPath($Ownership.config_path)) {
        throw 'Acceptance VM identity or configuration path changed.'
    }
    Assert-ScopedPath $Machine.Path | Out-Null
    Assert-OwnedDiskChain $Machine $Ownership
    return $Machine
}

function Assert-VmConfiguration($Machine) {
    $Memory = Get-VMMemory -VM $Machine
    $Firmware = Get-VMFirmware -VM $Machine
    $Security = Get-VMSecurity -VM $Machine
    $Disks = @(Get-VMHardDiskDrive -VM $Machine)
    $Adapters = @(Get-VMNetworkAdapter -VM $Machine)
    if ($Machine.Generation -ne 2 -or $Machine.ProcessorCount -ne 4 -or -not $Memory.DynamicMemoryEnabled -or
        $Memory.Startup -ne 4GB -or $Memory.Minimum -ne 4GB -or $Memory.Maximum -ne 8GB -or
        [string]$Firmware.SecureBoot -ne 'On' -or -not $Security.TpmEnabled -or
        $Disks.Count -ne 1 -or $Adapters.Count -ne 1 -or $Adapters[0].SwitchName -ne 'Default Switch') {
        throw 'Acceptance VM configuration differs from the approved generation, CPU, memory, firmware, TPM, disk or network contract.'
    }
    Assert-OwnedDiskChain $Machine (Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json)
}

function Get-CleanGuestEvidence($Machine) {
    Assert-VmConfiguration $Machine
    if ([string]$Machine.State -ne 'Running') { throw 'The acceptance guest must be running and have completed normal Windows setup.' }
    if (-not $GuestCredential) { throw 'Supply a valid guest PSCredential from Get-Credential; credentials are never stored by this harness.' }
    $Output = Invoke-Command -VMId $Machine.Id -Credential $GuestCredential -FilePath (Join-Path $PSScriptRoot 'hyperv-acceptance-guest.ps1')
    $Evidence = ($Output -join "`n") | ConvertFrom-Json
    $EvidencePath = Join-Path $VmRoot ('guest-baseline-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '.json')
    Assert-ScopedPath $EvidencePath | Out-Null
    $Evidence | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $EvidencePath -Encoding UTF8
    if (-not $Evidence.passed) { throw "Guest is not a verified clean baseline. Inspect $EvidencePath" }
    return [pscustomobject]@{ path = $EvidencePath; evidence = $Evidence }
}

# The bootstrap coordinator reuses these identity checks without invoking an action.
if ($MyInvocation.InvocationName -eq '.') { return }
Import-Module Hyper-V -ErrorAction Stop
Assert-ScopedPath $VmRoot | Out-Null
if ($Action -eq 'Inventory') {
    [ordered]@{
        observed_at = [DateTime]::UtcNow.ToString('o')
        target_name = $VmName
        target_path = $VmRoot
        target_exists = [bool](Get-VM -Name $VmName -ErrorAction SilentlyContinue)
        vms = @(Get-VM | Select-Object Name,Id,State,Generation,Path,ProcessorCount,MemoryAssigned)
        switches = @(Get-VMSwitch | Select-Object Name,SwitchType)
        host_free_memory_bytes = [long](Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory * 1KB
        workspace_free_bytes = (Get-PSDrive -Name ([IO.Path]::GetPathRoot($Workspace).TrimEnd(':','\'))).Free
    } | ConvertTo-Json -Depth 6
    return
}

if ($Action -eq 'Create') {
    if (Get-VM -Name $VmName -ErrorAction SilentlyContinue) { throw 'The exact acceptance VM name already exists; refusing to adopt or change it.' }
    if (Test-Path -LiteralPath $VmRoot) { throw 'The acceptance target directory already exists; review its contents before any new creation.' }
    Get-VMSwitch -Name 'Default Switch' | Out-Null
    $DriveName = [IO.Path]::GetPathRoot($Workspace).TrimEnd(':','\')
    if ((Get-PSDrive -Name $DriveName).Free -lt 80GB) { throw 'At least 80 GiB of free workspace storage is required.' }
    $ConfigPath = Assert-ScopedPath (Join-Path $VmRoot 'config')
    $DiskDirectory = Assert-ScopedPath (Join-Path $VmRoot 'disks')
    $DiskPath = Assert-ScopedPath (Join-Path $DiskDirectory 'Windows11-Acceptance.vhdx')
    New-Item -ItemType Directory -Path $VmRoot,$ConfigPath,$DiskDirectory | Out-Null
    $Ownership = [ordered]@{ name = $VmName; root = $VmRoot; config_path = $ConfigPath; disk_path = $DiskPath;
        created_at = [DateTime]::UtcNow.ToString('o'); state = 'creating'; vm_id = $null;
        clean_checkpoint_verified = $false; media_sha256 = $null; media_path = $null }
    Save-Ownership $Ownership
    # No existing VM is adopted. Failure leaves only this new VM and its scoped
    # artifacts for inspection; there is no automatic recursive deletion.
    $Machine = New-VM -Name $VmName -Generation 2 -MemoryStartupBytes 4GB -Path $ConfigPath -NewVHDPath $DiskPath -NewVHDSizeBytes 64GB -SwitchName 'Default Switch'
    $Ownership.vm_id = $Machine.Id.ToString()
    $Ownership.config_path = $Machine.Path
    Save-Ownership $Ownership
    Set-VMProcessor -VM $Machine -Count 4
    Set-VMMemory -VM $Machine -DynamicMemoryEnabled $true -StartupBytes 4GB -MinimumBytes 4GB -MaximumBytes 8GB
    Set-VMFirmware -VM $Machine -EnableSecureBoot On -SecureBootTemplate MicrosoftWindows
    Set-VMKeyProtector -VM $Machine -NewLocalKeyProtector
    Enable-VMTPM -VM $Machine
    Set-VM -VM $Machine -AutomaticStartAction Nothing -AutomaticStopAction ShutDown -AutomaticCheckpointsEnabled $false -CheckpointType ProductionOnly
    $Machine = Get-VM -Id $Machine.Id
    Assert-VmConfiguration $Machine
    $Ownership.state = 'created_off_without_os'
    Save-Ownership $Ownership
    $Ownership | ConvertTo-Json -Depth 6
    return
}

$Machine = Get-OwnedVm
$Ownership = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($Action -eq 'AttachMedia') {
    if (-not $IsoPath) { throw 'Supply the registered, downloaded official evaluation ISO path.' }
    if ([string]$Machine.State -ne 'Off') { throw 'Acceptance VM must be off before attaching installation media.' }
    $Media = Assert-ScopedMediaPath $IsoPath
    if ((Get-FileHash -LiteralPath $Media -Algorithm SHA256).Hash -ne $OfficialHash) {
        throw 'Media does not match the verified Microsoft Windows 11 Enterprise 25H2 EN-US x64 evaluation ISO.'
    }
    $Dvd = @(Get-VMDvdDrive -VM $Machine)
    if ($Dvd.Count -gt 1) { throw 'Unexpected multiple DVD devices; refusing to replace media.' }
    if ($Dvd.Count -eq 0) { $Dvd = @(Add-VMDvdDrive -VM $Machine -Path $Media -Passthru) }
    else { Set-VMDvdDrive -VMDvdDrive $Dvd[0] -Path $Media }
    Set-VMFirmware -VM $Machine -FirstBootDevice $Dvd[0]
    $Ownership.media_sha256 = $OfficialHash
    $Ownership.media_path = $Media
    $Ownership.state = 'media_attached_os_setup_pending'
    Save-Ownership $Ownership
    $Ownership | ConvertTo-Json -Depth 6
    return
}
if ($Action -eq 'Start') {
    Assert-VmConfiguration $Machine
    if ([string]$Machine.State -ne 'Off') { throw 'Acceptance VM is not off; no start operation was performed.' }
    Assert-RecordedMedia $Machine $Ownership
    $FreeMemory = [long](Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory * 1KB
    if ($FreeMemory -lt 6GB) { throw 'Starting the 4 GiB guest requires at least 6 GiB free host RAM, preserving a 2 GiB reserve. Existing VMs are never stopped.' }
    Start-VM -VM $Machine
    Get-VM -Id $Machine.Id | Select-Object Name,Id,State,MemoryAssigned | ConvertTo-Json
    return
}

$Clean = Get-CleanGuestEvidence $Machine
if ($Action -eq 'VerifyClean') { $Clean | ConvertTo-Json -Depth 10; return }
if ($Action -eq 'Checkpoint') {
    if (Get-VMCheckpoint -VM $Machine -Name $CheckpointName -ErrorAction SilentlyContinue) { throw 'The clean checkpoint already exists; it will not be replaced.' }
    Checkpoint-VM -VM $Machine -SnapshotName $CheckpointName
    $Checkpoint = Get-VMCheckpoint -VM $Machine -Name $CheckpointName
    $Ownership | Add-Member -NotePropertyName checkpoint_id -NotePropertyValue $Checkpoint.Id.ToString() -Force
    $Ownership | Add-Member -NotePropertyName baseline_evidence -NotePropertyValue $Clean.path -Force
    $Ownership.clean_checkpoint_verified = $true
    $Ownership.state = 'clean_guest_checkpoint_verified'
    Save-Ownership $Ownership
    $Ownership | ConvertTo-Json -Depth 6
}
