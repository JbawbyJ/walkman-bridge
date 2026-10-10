#requires -Version 7.2
<#
Preparation only: no Hyper-V, mount, disk, network, policy or guest commands.
An answer ISO contains a recoverable local password and a disk-0 installation
recipe. It must only be attached by the separately reviewed host coordinator.
Dot-source this file to import Get-AcceptanceCredential without running an action.
#>
[CmdletBinding()]
param(
    [ValidateSet('Describe','Prepare','Verify')][string]$Action = 'Describe',
    [string]$InstallationIsoPath,
    [string]$ImageName,
    [string]$PackagePath
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$AcceptanceVmName = 'Red-Lotus-Windows-Acceptance'
$AcceptanceWorkspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$AcceptanceVmRoot = Join-Path $AcceptanceWorkspace ('hyperv\' + $AcceptanceVmName)
$AcceptanceBootstrapRoot = Join-Path $AcceptanceVmRoot 'bootstrap'
$AcceptanceOfficialHash = 'A61ADEAB895EF5A4DB436E0A7011C92A2FF17BB0357F58B13BBC4062E535E7B9'

function Assert-AcceptanceChildPath([string]$Path, [string]$Parent) {
    if (-not [IO.Path]::IsPathFullyQualified($Path) -or $Path.StartsWith('\\') -or $Path.Substring(2).Contains(':')) {
        throw 'A fully qualified local path without streams is required.'
    }
    $Full = [IO.Path]::GetFullPath($Path)
    $Root = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
    if (-not $Full.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Path is outside the scoped preparation directory.' }
    for ($Ancestor = $Full; $Ancestor; $Ancestor = [IO.Path]::GetDirectoryName($Ancestor)) {
        if (Test-Path -LiteralPath $Ancestor) {
            if ((Get-Item -LiteralPath $Ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Reparse points are not permitted in preparation paths.'
            }
        }
    }
    return $Full
}

function New-AcceptancePrivateDirectory([string]$Path) {
    if (Test-Path -LiteralPath $Path) { throw 'Preparation refuses to reuse an existing directory.' }
    $Parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Path))
    Assert-AcceptanceChildPath $Path $Parent | Out-Null
    if (-not (Test-Path -LiteralPath $Parent -PathType Container)) { throw 'The parent directory must already exist.' }
    $Acl = [Security.AccessControl.DirectorySecurity]::new()
    $Acl.SetAccessRuleProtection($true, $false)
    $Owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $Acl.SetOwner($Owner)
    foreach ($Sid in @($Owner, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
        $Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($Sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow))
    }
    # Atomic creation with restrictive ACL, before any sensitive bytes exist.
    [IO.FileSystemAclExtensions]::Create([IO.DirectoryInfo]::new($Path), $Acl)
    Assert-AcceptancePrivateAcl $Path
}

function Assert-AcceptancePrivateAcl([string]$Path, [string]$OptionalVmReadSid = '') {
    $Acl = Get-Acl -LiteralPath $Path
    $CurrentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $CurrentSid) { throw 'Preparation material is not owned by the current user.' }
    $Allowed = @($CurrentSid, 'S-1-5-18')
    $HasOwner = $false
    $HasVmRead = $false
    $VmGrantsAreReadOnly = $true
    $HasVmCapability = $false
    # Hyper-V adds this fixed worker AppContainer capability after DVD attach.
    # It is NOT a VM identity. Windows checks AppContainer capabilities and
    # normal user/group identity conjunctively; the exact VM SID must remain.
    # https://learn.microsoft.com/windows/win32/secauthz/implementing-an-appcontainer
    $VmCapabilitySid = 'S-1-15-3-1024-2268835264-3721307629-241982045-173645152-1490879176-104643441-2915960892-1612460704'
    $ReadOnlyMask = [Security.AccessControl.FileSystemRights]::Read -bor [Security.AccessControl.FileSystemRights]::Synchronize
    $IsAnswer = [IO.Path]::GetFileName($Path) -eq 'answer.iso' -and -not (Get-Item -LiteralPath $Path -Force).PSIsContainer
    foreach ($Rule in $Acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($Rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow) {
            if ($Rule.IdentityReference.Value -notin $Allowed) {
                if ($Rule.IdentityReference.Value -eq $VmCapabilitySid) {
                    if (-not $IsAnswer -or -not $OptionalVmReadSid -or $Rule.IsInherited -or
                        $Rule.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]::None -or
                        $Rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None -or
                        $Rule.FileSystemRights -ne $ReadOnlyMask) { throw 'Worker capability must be explicit read-only access on the exact answer media.' }
                    $HasVmCapability = $true
                    continue
                }
                $ReadMask = [Security.AccessControl.FileSystemRights]::ReadAndExecute -bor [Security.AccessControl.FileSystemRights]::Synchronize
                if (-not $IsAnswer -or -not $OptionalVmReadSid -or $Rule.IdentityReference.Value -ne $OptionalVmReadSid -or
                    ($Rule.FileSystemRights -band (-bnot $ReadMask)) -ne 0 -or
                    (Get-Item -LiteralPath $Path -Force).PSIsContainer) { throw 'Preparation material grants access to an unexpected identity or grants excessive VM rights.' }
                $ThisVmGrantIsReadOnly = -not $Rule.IsInherited -and
                    $Rule.InheritanceFlags -eq [Security.AccessControl.InheritanceFlags]::None -and
                    $Rule.PropagationFlags -eq [Security.AccessControl.PropagationFlags]::None -and
                    $Rule.FileSystemRights -eq $ReadOnlyMask
                $HasVmRead = $HasVmRead -or $ThisVmGrantIsReadOnly
                $VmGrantsAreReadOnly = $VmGrantsAreReadOnly -and $ThisVmGrantIsReadOnly
            }
            if ($Rule.IdentityReference.Value -eq $CurrentSid -and
                ($Rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq [Security.AccessControl.FileSystemRights]::FullControl) { $HasOwner = $true }
        }
    }
    if ($HasVmCapability -and (-not $HasVmRead -or -not $VmGrantsAreReadOnly)) { throw 'Worker capability requires the paired exact VM identity with explicit read-only access.' }
    if (-not $HasOwner) { throw 'Preparation material lacks the required owner access.' }
    if ((Get-Item -LiteralPath $Path -Force).PSIsContainer -and -not $Acl.AreAccessRulesProtected) { throw 'Private directory inherits external access.' }
}

function New-AcceptanceLocalCredential {
    $Random = [byte[]]::new(32)
    [Security.Cryptography.RandomNumberGenerator]::Fill($Random)
    # Fixed category prefix meets local complexity rules; entropy is 256 bits.
    $Secret = 'Aa9!' + [Convert]::ToBase64String($Random)
    try { return [PSCredential]::new('RedLotusAcceptance', (ConvertTo-SecureString $Secret -AsPlainText -Force)) }
    finally { [Array]::Clear($Random); $Secret = $null }
}

function New-AcceptanceAnswerBytes([PSCredential]$Credential, [string]$SelectedImageName) {
    if ($SelectedImageName -ne 'Windows 11 Enterprise Evaluation') { throw 'Supply the exact Windows 11 Enterprise Evaluation image name verified from the official media.' }
    if ($Credential.UserName -ne 'RedLotusAcceptance') { throw 'Only the generic local acceptance account is supported.' }
    $Password = $Credential.GetNetworkCredential().Password
    if ($Password.Length -lt 32) { throw 'The generated local password must have at least 32 characters.' }
    try {
        $EscapedPassword = [Security.SecurityElement]::Escape($Password)
        $EscapedImage = [Security.SecurityElement]::Escape($SelectedImageName)
        # Standard Microsoft unattend settings only. No commands, AutoLogon,
        # remote account, product key, Defender/UAC policy, or OOBE bypass.
        $Answer = @"
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <SetupUILanguage><UILanguage>en-US</UILanguage></SetupUILanguage><InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale><UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale>
    </component>
    <component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <DiskConfiguration><Disk wcm:action="add"><DiskID>0</DiskID><WillWipeDisk>false</WillWipeDisk>
        <CreatePartitions>
          <CreatePartition wcm:action="add"><Order>1</Order><Type>EFI</Type><Size>512</Size></CreatePartition>
          <CreatePartition wcm:action="add"><Order>2</Order><Type>MSR</Type><Size>16</Size></CreatePartition>
          <CreatePartition wcm:action="add"><Order>3</Order><Type>Primary</Type><Size>63744</Size></CreatePartition>
          <CreatePartition wcm:action="add"><Order>4</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>
        </CreatePartitions>
        <ModifyPartitions>
          <ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Format>FAT32</Format><Label>System</Label></ModifyPartition>
          <ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>3</PartitionID><Format>NTFS</Format><Label>Windows</Label><Letter>C</Letter></ModifyPartition>
          <ModifyPartition wcm:action="add"><Order>3</Order><PartitionID>4</PartitionID><Format>NTFS</Format><Label>Recovery</Label><TypeID>de94bba4-06d1-4d40-a16a-bfd50179d6ac</TypeID></ModifyPartition>
        </ModifyPartitions>
      </Disk><WillShowUI>OnError</WillShowUI></DiskConfiguration>
      <ImageInstall><OSImage><InstallFrom><MetaData wcm:action="add"><Key>/IMAGE/NAME</Key><Value>$EscapedImage</Value></MetaData></InstallFrom><InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo><WillShowUI>OnError</WillShowUI></OSImage></ImageInstall>
      <UserData><AcceptEula>true</AcceptEula></UserData>
    </component>
  </settings>
  <settings pass="specialize"><component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><ComputerName>RL-ACCEPTANCE</ComputerName><TimeZone>UTC</TimeZone></component></settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-International-Core" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale><UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale></component>
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <OOBE><HideOnlineAccountScreens>true</HideOnlineAccountScreens><HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE><ProtectYourPC>1</ProtectYourPC></OOBE>
      <UserAccounts><LocalAccounts><LocalAccount wcm:action="add"><Name>RedLotusAcceptance</Name><DisplayName>Red Lotus Acceptance</DisplayName><Description>Dedicated local Windows acceptance test account</Description><Group>Administrators</Group><Password><Value>$EscapedPassword</Value><PlainText>true</PlainText></Password></LocalAccount></LocalAccounts></UserAccounts>
    </component>
  </settings>
</unattend>
"@
        return ,([Text.UTF8Encoding]::new($false).GetBytes($Answer))
    } finally { $Password = $null; $EscapedPassword = $null; $Answer = $null }
}

function Initialize-AcceptanceIsoInterop {
    if ('RedLotusAcceptanceIso' -as [type]) { return }
    # In-process Windows COM interop; no installed toolchain or guest software.
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
[ComVisible(true), ClassInterface(ClassInterfaceType.None)]
public sealed class RedLotusAcceptanceInput : IStream, IDisposable {
    private readonly MemoryStream stream;
    public RedLotusAcceptanceInput(byte[] bytes) { stream = new MemoryStream(bytes, false); }
    public void Read(byte[] b, int n, IntPtr p) { int read=stream.Read(b,0,n); if(p!=IntPtr.Zero) Marshal.WriteInt32(p,read); }
    public void Seek(long o,int origin,IntPtr p) { long pos=stream.Seek(o,(SeekOrigin)origin); if(p!=IntPtr.Zero) Marshal.WriteInt64(p,pos); }
    public void Stat(out STATSTG s,int flags) { s=new STATSTG(); s.type=2; s.cbSize=stream.Length; }
    public void Write(byte[] b,int n,IntPtr p) { throw new NotSupportedException(); }
    public void SetSize(long n) { throw new NotSupportedException(); }
    public void CopyTo(IStream target,long n,IntPtr r,IntPtr w) { throw new NotSupportedException(); }
    public void Commit(int flags) { }
    public void Revert() { throw new NotSupportedException(); }
    public void LockRegion(long o,long n,int t) { throw new NotSupportedException(); }
    public void UnlockRegion(long o,long n,int t) { throw new NotSupportedException(); }
    public void Clone(out IStream copy) { throw new NotSupportedException(); }
    public void Dispose() { stream.Dispose(); }
}
public static class RedLotusAcceptanceIso {
    public static void Save(object source,string path,long expected) {
        if(expected<=0 || expected>32*1024*1024) throw new IOException("Unexpected answer ISO size.");
        IStream stream=(IStream)source;
        byte[] bytes=new byte[65536];
        IntPtr count=Marshal.AllocHGlobal(4);
        try {
            stream.Seek(0,0,IntPtr.Zero);
            using(var output=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
                long remaining=expected;
                while(remaining>0) {
                    int wanted=(int)Math.Min(bytes.Length,remaining);
                    Marshal.WriteInt32(count,0); stream.Read(bytes,wanted,count);
                    int read=Marshal.ReadInt32(count);
                    if(read<=0 || read>wanted) throw new IOException("Incomplete answer ISO stream.");
                    output.Write(bytes,0,read); remaining-=read;
                }
                output.Flush(true);
            }
        } finally { Array.Clear(bytes,0,bytes.Length); Marshal.FreeHGlobal(count); }
    }
}
'@
}

function New-AcceptanceAnswerIso([byte[]]$AnswerBytes, [string]$IsoPath, [string]$PrivateDirectory) {
    Assert-AcceptanceChildPath $IsoPath $PrivateDirectory | Out-Null
    Assert-AcceptancePrivateAcl $PrivateDirectory
    if (Test-Path -LiteralPath $IsoPath) { throw 'An existing answer ISO is never overwritten.' }
    if (-not $AnswerBytes -or $AnswerBytes.Length -gt 1MB) { throw 'Unexpected answer XML length.' }
    Initialize-AcceptanceIsoInterop
    $Builder = $null; $Result = $null; $Stream = $null; $InputStream = $null; $Root = $null
    try {
        $Builder = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
        $Builder.ChooseImageDefaultsForMediaType(2) # IMAPI_MEDIA_TYPE_CDR; no physical device access.
        $Builder.FileSystemsToCreate = 3 # ISO9660 and Joliet.
        $Builder.ISO9660InterchangeLevel = 2
        $Builder.SessionStartBlock = 0
        $Builder.VolumeName = 'RL_ACCEPTANCE'
        $Builder.WorkingDirectory = $PrivateDirectory
        $Builder.StageFiles = $false
        $InputStream = [RedLotusAcceptanceInput]::new($AnswerBytes)
        $Root = $Builder.Root
        $Root.AddFile('Autounattend.xml', $InputStream)
        $Result = $Builder.CreateResultImage()
        $Stream = $Result.ImageStream
        [RedLotusAcceptanceIso]::Save($Stream, $IsoPath, ([long]$Result.TotalBlocks * [long]$Result.BlockSize))
        Assert-AcceptancePrivateAcl $IsoPath
    } finally {
        foreach ($ComObject in @($Stream,$Result,$Root,$Builder)) {
            if ($null -ne $ComObject -and [Runtime.InteropServices.Marshal]::IsComObject($ComObject)) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($ComObject) }
        }
        if ($InputStream) { $InputStream.Dispose() }
    }
}

function Get-AcceptancePreparationManifest([string]$Path) {
    $Full = Assert-AcceptanceChildPath $Path $AcceptanceBootstrapRoot
    if ([IO.Path]::GetDirectoryName($Full) -ne $AcceptanceBootstrapRoot -or
        [IO.Path]::GetFileName($Full) -notmatch '^setup-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}$') { throw 'Expected one recorded preparation package beneath the fixed bootstrap directory.' }
    Assert-AcceptancePrivateAcl $AcceptanceBootstrapRoot
    Assert-AcceptancePrivateAcl $Full
    $ManifestFile = Assert-AcceptanceChildPath (Join-Path $Full 'manifest.json') $Full
    Assert-AcceptancePrivateAcl $ManifestFile
    $Manifest = Get-Content -LiteralPath $ManifestFile -Raw | ConvertFrom-Json
    $Owner = Get-AcceptanceOwnedConfiguration
    if ($Manifest.schema -ne 1 -or $Manifest.state -ne 'prepared_not_attached' -or $Manifest.package_path -ne $Full -or
        $Manifest.vm_name -ne $AcceptanceVmName -or $Manifest.vm_id -ne $Owner.vm_id -or
        $Manifest.user_sid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -or
        $Manifest.installation_iso_sha256 -ne $AcceptanceOfficialHash) { throw 'Preparation identity or state mismatch.' }
    foreach ($File in @('answer.iso','credential.xml')) {
        $FilePath = Assert-AcceptanceChildPath (Join-Path $Full $File) $Full
        $VmReadSid = ''
        if ($File -eq 'answer.iso') {
            # The host coordinator may later grant this exact VM read access to
            # its answer DVD. Never grant VM access to the protected credential.
            try { $VmReadSid = ([Security.Principal.NTAccount]::new('NT VIRTUAL MACHINE', $Manifest.vm_id)).Translate([Security.Principal.SecurityIdentifier]).Value }
            catch [Security.Principal.IdentityNotMappedException] { $VmReadSid = '' }
        }
        Assert-AcceptancePrivateAcl $FilePath $VmReadSid
        if ((Get-FileHash -LiteralPath $FilePath -Algorithm SHA256).Hash -ne $Manifest.files.$File) { throw 'Preparation file hash mismatch.' }
    }
    return $Manifest
}

function Get-AcceptanceOwnedConfiguration {
    $OwnerPath = Assert-AcceptanceChildPath (Join-Path $AcceptanceVmRoot 'ownership.json') $AcceptanceVmRoot
    $Owner = Get-Content -LiteralPath $OwnerPath -Raw | ConvertFrom-Json
    $ParsedId = [Guid]::Empty
    if ($Owner.name -ne $AcceptanceVmName -or $Owner.root -ne $AcceptanceVmRoot -or
        -not [Guid]::TryParse([string]$Owner.vm_id, [ref]$ParsedId) -or $ParsedId -eq [Guid]::Empty -or
        $Owner.disk_path -ne (Join-Path $AcceptanceVmRoot 'disks\Windows11-Acceptance.vhdx')) { throw 'The ownership record does not match the fixed acceptance target.' }
    return $Owner
}

function Get-AcceptanceCredential([string]$PackagePath) {
    Get-AcceptancePreparationManifest $PackagePath | Out-Null
    $Credential = Import-Clixml -LiteralPath (Join-Path $PackagePath 'credential.xml')
    if ($Credential -isnot [PSCredential] -or $Credential.UserName -ne 'RedLotusAcceptance') { throw 'Invalid protected acceptance credential.' }
    return $Credential
}

function Invoke-AcceptancePreparation {
    if ($Action -eq 'Describe') {
        return [pscustomobject]@{state='preparation_only'; vm_name=$AcceptanceVmName; bootstrap_root=$AcceptanceBootstrapRoot;
            requires='Official ISO, exact image name verified from its WIM metadata, existing ownership manifest; host attachment and guest cleanup are separate reviewed steps.';
            guest_ready=$false; vm_mutations=$false}
    }
    if ($Action -eq 'Verify') {
        $Manifest = Get-AcceptancePreparationManifest $PackagePath
        $Credential = Get-AcceptanceCredential $PackagePath
        $Credential = $null
        return [pscustomobject]@{state=$Manifest.state; package_path=$Manifest.package_path; vm_id=$Manifest.vm_id; integrity_verified=$true; guest_ready=$false}
    }
    if ($ImageName -ne 'Windows 11 Enterprise Evaluation') { throw 'Prepare requires the Windows 11 Enterprise Evaluation image name verified from the official WIM metadata.' }
    $Owner = Get-AcceptanceOwnedConfiguration
    if ($Owner.clean_checkpoint_verified) { throw 'A verified clean guest is not eligible for first installation preparation.' }
    $Iso = Assert-AcceptanceChildPath $InstallationIsoPath (Join-Path $AcceptanceWorkspace 'hyperv\media')
    if ([IO.Path]::GetExtension($Iso) -ne '.iso' -or (Get-FileHash -LiteralPath $Iso -Algorithm SHA256).Hash -ne $AcceptanceOfficialHash) { throw 'Installation ISO does not match the pinned official Microsoft hash.' }
    if (-not (Test-Path -LiteralPath $AcceptanceBootstrapRoot)) { New-AcceptancePrivateDirectory $AcceptanceBootstrapRoot }
    Assert-AcceptanceChildPath $AcceptanceBootstrapRoot $AcceptanceVmRoot | Out-Null
    Assert-AcceptancePrivateAcl $AcceptanceBootstrapRoot
    # Do not replace or obscure an earlier attempt, including partial failures.
    if (@(Get-ChildItem -LiteralPath $AcceptanceBootstrapRoot -Force).Count -ne 0) { throw 'Existing bootstrap material requires coordinated review; preparation does not overwrite or delete it.' }
    $Destination = Join-Path $AcceptanceBootstrapRoot ('setup-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N'))
    New-AcceptancePrivateDirectory $Destination
    $Credential = $null; $AnswerBytes = $null
    try {
        $Credential = New-AcceptanceLocalCredential
        $Credential | Export-Clixml -LiteralPath (Join-Path $Destination 'credential.xml') -Encoding utf8
        $AnswerBytes = New-AcceptanceAnswerBytes $Credential $ImageName
        New-AcceptanceAnswerIso $AnswerBytes (Join-Path $Destination 'answer.iso') $Destination
        $Hashes = [ordered]@{}
        foreach ($File in @('answer.iso','credential.xml')) { $Hashes[$File] = (Get-FileHash -LiteralPath (Join-Path $Destination $File) -Algorithm SHA256).Hash }
        $Manifest = [ordered]@{schema=1; state='prepared_not_attached'; created_at=[DateTime]::UtcNow.ToString('o');
            vm_name=$AcceptanceVmName; vm_id=$Owner.vm_id; package_path=$Destination;
            user_sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; local_account='RedLotusAcceptance';
            installation_iso_path=$Iso; installation_iso_sha256=$AcceptanceOfficialHash; image_name=$ImageName;
            image_name_source='caller verified WIM metadata from the official hash-bound ISO'; guest_disk_id=0; guest_disk_bytes=64GB;
            answer_contains_local_password=$true; credential_protection='Windows CurrentUser DPAPI';
            guest_cleanup_verified=$false; media_detached_verified=$false; files=$Hashes}
        $Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Destination 'manifest.json') -Encoding utf8
        Get-AcceptancePreparationManifest $Destination | Out-Null
        return [pscustomobject]@{state='prepared_not_attached'; package_path=$Destination; vm_id=$Owner.vm_id; guest_ready=$false}
    } catch {
        # Never include XML, decrypted passwords, or COM argument details in logs.
        # Partial private files remain available for explicitly scoped review.
        throw 'Private preparation failed. No VM was changed; inspect the private package without logging its sensitive contents.'
    } finally {
        if ($AnswerBytes) { [Array]::Clear($AnswerBytes) }
        $Credential = $null
    }
}

if ($MyInvocation.InvocationName -ne '.') { Invoke-AcceptancePreparation | ConvertTo-Json -Depth 6 }
