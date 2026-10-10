#requires -Version 7.2
[CmdletBinding()]
param(
    [ValidateSet('Describe','Attach','Start')][string]$BootstrapAction = 'Describe',
    [string]$PackagePath,
    [switch]$EvaluationRegistrationConfirmed
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$RequestedBootstrapPackagePath = $PackagePath
. (Join-Path $PSScriptRoot 'hyperv-acceptance.ps1')
. (Join-Path $PSScriptRoot 'prepare-acceptance-unattend.ps1')
$PackagePath = $RequestedBootstrapPackagePath

function Get-BootstrapFreshDisk($Machine, $Owner) {
    Assert-VmConfiguration $Machine
    if ([string]$Machine.State -ne 'Off' -or $Owner.clean_checkpoint_verified -or
        $Owner.state -notin @('created_off_without_os','media_attached_os_setup_pending','bootstrap_attached')) {
        throw 'Only the owned first-installation VM, powered off, is eligible.'
    }
    if (@(Get-VMCheckpoint -VM $Machine -ErrorAction Stop).Count) { throw 'Existing checkpoints prevent first-installation bootstrap.' }
    $Disks = @(Get-VMHardDiskDrive -VM $Machine -ErrorAction Stop)
    if ($Disks.Count -ne 1 -or $Disks[0].ControllerNumber -ne 0 -or $Disks[0].ControllerLocation -ne 0 -or
        [string]$Disks[0].ControllerType -ne 'SCSI' -or $Disks[0].Path -ne $Owner.disk_path) {
        throw 'The sole owned installation disk must occupy SCSI 0:0.'
    }
    $Disk = Get-VHD -Path (Assert-OwnedDiskPath $Disks[0].Path) -ErrorAction Stop
    # This VM's recorded initial VHDX is 4 MiB of container metadata. No
    # populated/expanded disk, parent chain or mounted disk is accepted.
    if ($Disk.Size -ne 64GB -or $Disk.FileSize -ne 4MB -or $Disk.Attached -or
        [string]$Disk.VhdType -ne 'Dynamic' -or $Disk.ParentPath) { throw 'Disk differs from the fresh, unmounted first-installation container.' }
    return [pscustomobject]@{path=$Owner.disk_path;sha256=(Get-FileHash -LiteralPath $Owner.disk_path -Algorithm SHA256).Hash;identifier=[string]$Disk.DiskIdentifier}
}

function Assert-BootstrapMedia($Machine, $Owner, $Preparation) {
    if ($Preparation.vm_id -ne $Machine.Id.ToString() -or $Preparation.guest_disk_id -ne 0 -or
        $Preparation.guest_disk_bytes -ne 64GB) { throw 'Preparation targets a different VM or disk.' }
    $Installer = Assert-ScopedMediaPath $Preparation.installation_iso_path
    if ((Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash -ne $OfficialHash) { throw 'Installation media hash changed.' }
    $Answer = Join-Path $Preparation.package_path 'answer.iso'
    $Dvds = @(Get-VMDvdDrive -VM $Machine -ErrorAction Stop)
    if ($Dvds.Count -ne 2 -or @($Dvds | Where-Object Path -eq $Installer).Count -ne 1 -or
        @($Dvds | Where-Object Path -eq $Answer).Count -ne 1) { throw 'Only the exact installation and private answer DVDs are permitted.' }
    if ($Owner.bootstrap.package_path -ne $Preparation.package_path -or
        $Owner.bootstrap.manifest_sha256 -ne (Get-FileHash -LiteralPath (Join-Path $Preparation.package_path 'manifest.json')).Hash -or
        $Owner.bootstrap.answer_sha256 -ne $Preparation.files.'answer.iso') { throw 'Recorded bootstrap package changed.' }
}

if ($MyInvocation.InvocationName -eq '.') { return }
if ($BootstrapAction -eq 'Describe') {
    [ordered]@{vm_name=$VmName;actions=@('Attach','Start');default_mutations=$false;registration_required_for_start=$true;
        requirements='Verified first-installation package, unchanged fresh owned disk, exact media, sufficient host RAM. Start is one-shot; never automatically retry Windows writes.'} | ConvertTo-Json
    return
}
Import-Module Hyper-V -ErrorAction Stop
$Machine = Get-OwnedVm
$Owner = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$Preparation = Get-AcceptancePreparationManifest $PackagePath
if ($Preparation.vm_id -ne $Machine.Id.ToString()) { throw 'Preparation and live VM identities differ.' }
$FreshDisk = Get-BootstrapFreshDisk $Machine $Owner
$Installer = Assert-ScopedMediaPath $Preparation.installation_iso_path
if ((Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash -ne $OfficialHash) { throw 'Official installer media changed.' }
$Answer = Join-Path $Preparation.package_path 'answer.iso'

if ($BootstrapAction -eq 'Attach') {
    if ($Owner.state -eq 'bootstrap_attached' -or $Owner.PSObject.Properties['bootstrap']) { throw 'A bootstrap attempt is already recorded; inspect instead of replacing it.' }
    $Dvds = @(Get-VMDvdDrive -VM $Machine -ErrorAction Stop)
    if ($Dvds.Count -gt 1 -or ($Dvds.Count -eq 1 -and $Dvds[0].Path -and $Dvds[0].Path -ne $Installer)) { throw 'An unexpected DVD is attached; refusing replacement.' }
    if ($Dvds.Count -eq 0) { $InstallDvd = Add-VMDvdDrive -VM $Machine -Path $Installer -Passthru }
    else { Set-VMDvdDrive -VMDvdDrive $Dvds[0] -Path $Installer; $InstallDvd = $Dvds[0] }
    # Grant only this VM read access to its private answer media, never to the
    # DPAPI credential file. SYSTEM and the creator retain their existing ACLs.
    $VmSid = [Security.Principal.NTAccount]::new('NT VIRTUAL MACHINE', $Machine.Id.ToString()).Translate([Security.Principal.SecurityIdentifier])
    $Acl = Get-Acl -LiteralPath $Answer
    $Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($VmSid,[Security.AccessControl.FileSystemRights]::Read,[Security.AccessControl.AccessControlType]::Allow))
    Set-Acl -LiteralPath $Answer -AclObject $Acl
    Get-AcceptancePreparationManifest $PackagePath | Out-Null
    Add-VMDvdDrive -VM $Machine -Path $Answer | Out-Null
    Set-VMFirmware -VM $Machine -FirstBootDevice $InstallDvd
    $Owner | Add-Member -NotePropertyName bootstrap -NotePropertyValue ([pscustomobject]@{
        package_path=$Preparation.package_path;manifest_sha256=(Get-FileHash -LiteralPath (Join-Path $PackagePath 'manifest.json')).Hash;
        answer_sha256=$Preparation.files.'answer.iso';disk_sha256=$FreshDisk.sha256;disk_identifier=$FreshDisk.identifier;
        attached_at=[DateTime]::UtcNow.ToString('o');installation_started=$false;guest_cleanup_verified=$false})
    $Owner.media_path=$Installer; $Owner.media_sha256=$OfficialHash; $Owner.state='bootstrap_attached'
    Assert-BootstrapMedia $Machine $Owner $Preparation
    Save-Ownership $Owner
    [ordered]@{state=$Owner.state;vm_name=$VmName;vm_id=$Machine.Id.ToString();powered_off=$true;package_verified=$true;guest_ready=$false} | ConvertTo-Json
    return
}

if (-not $EvaluationRegistrationConfirmed) { throw 'Confirm the evaluation registration prerequisite before starting Windows installation.' }
if ($Owner.state -ne 'bootstrap_attached' -or -not $Owner.PSObject.Properties['bootstrap'] -or $Owner.bootstrap.installation_started) { throw 'No unused, recorded bootstrap is available.' }
Assert-BootstrapMedia $Machine $Owner $Preparation
if ($FreshDisk.sha256 -ne $Owner.bootstrap.disk_sha256 -or $FreshDisk.identifier -ne $Owner.bootstrap.disk_identifier) { throw 'The initial disk changed after bootstrap attachment.' }
$FreeMemory = [long](Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory * 1KB
if ($FreeMemory -lt 6GB) { throw 'At least 6 GiB free host RAM is required before starting the 4 GiB guest. Existing workloads are preserved.' }
# Persist admission before the first possible write. A failed start is retained
# for diagnosis and is not automatically retried, because its outcome may vary.
$Owner.bootstrap.installation_started=$true
$Owner.bootstrap | Add-Member -NotePropertyName registration_confirmed -NotePropertyValue $true
$Owner.state='bootstrap_started'
Save-Ownership $Owner
Start-VM -VM $Machine -ErrorAction Stop
[ordered]@{state=$Owner.state;vm_name=$VmName;guest_ready=$false;next='Observe normal Windows setup; verify guest and remove private setup media before taking a clean checkpoint.'} | ConvertTo-Json
