#requires -Version 7.2
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Source = Join-Path $PSScriptRoot 'prepare-acceptance-unattend.ps1'
if (-not (Test-Path -LiteralPath $Source)) { throw 'RED: unattended preparation implementation is missing.' }
. $Source
$Passed = 0
function Assert-Case([bool]$Condition, [string]$Name) {
    if (-not $Condition) { throw "FAIL: $Name" }
    $script:Passed++
}
function Assert-Rejected([scriptblock]$Operation, [string]$Name) {
    $Rejected = $false
    try { & $Operation | Out-Null } catch { $Rejected = $true }
    Assert-Case $Rejected $Name
}

# Only newly generated synthetic material in a fresh, private test directory.
# Never import the real VM ownership manifest, inspect disks, or invoke Hyper-V.
$TestParent = Join-Path $PSScriptRoot 'build'
$TestRoot = Join-Path $TestParent ('unattend-test-' + [Guid]::NewGuid().ToString('N'))
New-AcceptancePrivateDirectory $TestRoot
$XmlBytes = $null; $Credential = $null; $Recovered = $null; $Secret = $null; $Answer = $null
try {
    $Credential = New-AcceptanceLocalCredential
    $Secret = $Credential.GetNetworkCredential().Password
    Assert-Case ($Credential.UserName -eq 'RedLotusAcceptance' -and $Secret.Length -ge 32) 'local test identity and strong generated secret'
    $XmlBytes = New-AcceptanceAnswerBytes $Credential 'Windows 11 Enterprise Evaluation'
    [xml]$Answer = [Text.Encoding]::UTF8.GetString($XmlBytes)
    $Ns = New-Object Xml.XmlNamespaceManager $Answer.NameTable
    $Ns.AddNamespace('u','urn:schemas-microsoft-com:unattend')
    Assert-Case ($Answer.SelectSingleNode('//u:AcceptEula', $Ns).InnerText -eq 'true') 'documented setup license acceptance'
    Assert-Case ($Answer.SelectSingleNode('//u:LocalAccount/u:Name', $Ns).InnerText -eq 'RedLotusAcceptance') 'standard local account setting'
    Assert-Case ($Answer.SelectSingleNode('//u:LocalAccount/u:Password/u:Value', $Ns).InnerText -eq $Secret) 'password round trip in sensitive in-memory answer'
    Assert-Case ($Answer.SelectSingleNode('//u:HideOnlineAccountScreens', $Ns).InnerText -eq 'true') 'supported online account screen setting'
    Assert-Case ($Answer.SelectNodes('//u:AutoLogon|//u:RunSynchronous|//u:FirstLogonCommands|//u:SkipMachineOOBE|//u:SkipUserOOBE', $Ns).Count -eq 0) 'no autologon or bypass commands'
    Assert-Case ($Answer.SelectSingleNode('//u:WillWipeDisk', $Ns).InnerText -eq 'false' -and $Answer.SelectSingleNode('//u:InstallTo/u:DiskID', $Ns).InnerText -eq '0') 'installation targets the reviewed single blank guest disk without wiping'
    Assert-Case ($Answer.SelectNodes('//u:CreatePartition', $Ns).Count -eq 4 -and $Answer.SelectSingleNode('//u:InstallTo/u:PartitionID', $Ns).InnerText -eq '3') 'UEFI GPT partitions and Windows target'
    Assert-Rejected { New-AcceptanceAnswerBytes $Credential '' } 'empty image selection rejected'
    Assert-Rejected { New-AcceptanceAnswerBytes $Credential 'Other Edition' } 'non-evaluation image selection rejected'

    $CredentialPath = Join-Path $TestRoot 'credential.xml'
    $Credential | Export-Clixml -LiteralPath $CredentialPath
    $Recovered = Import-Clixml -LiteralPath $CredentialPath
    Assert-Case ($Recovered.GetNetworkCredential().Password -eq $Secret) 'real CurrentUser DPAPI round trip'
    Assert-Case (-not ([IO.File]::ReadAllText($CredentialPath).Contains($Secret))) 'DPAPI file excludes plaintext secret'
    Assert-AcceptancePrivateAcl $TestRoot
    Assert-AcceptancePrivateAcl $CredentialPath
    $script:Passed++
    $Acl = Get-Acl -LiteralPath $TestRoot
    Assert-Case $Acl.AreAccessRulesProtected 'private directory disables inherited grants'
    Assert-Rejected { Assert-AcceptanceChildPath (Join-Path $TestRoot '..\sibling') $TestRoot } 'sibling path rejected'
    Assert-Rejected { Assert-AcceptanceChildPath ($TestRoot + ':stream') $TestRoot } 'alternate stream path rejected'

    $IsoPath = Join-Path $TestRoot 'answer.iso'
    New-AcceptanceAnswerIso $XmlBytes $IsoPath $TestRoot
    $Iso = [IO.File]::ReadAllBytes($IsoPath)
    Assert-Case ([Text.Encoding]::ASCII.GetString($Iso,16*2048+1,5) -eq 'CD001') 'real IMAPI ISO9660 primary volume'
    # Independent ISO9660 directory walk; no ISO mount and no production reader.
    $RootRecord = 16*2048+156
    $DirectoryStart = [BitConverter]::ToUInt32($Iso,$RootRecord+2)*2048
    $DirectoryLength = [BitConverter]::ToUInt32($Iso,$RootRecord+10)
    $Files = @()
    for ($Position = [int]$DirectoryStart; $Position -lt $DirectoryStart+$DirectoryLength;) {
        $Length = $Iso[$Position]
        if ($Length -eq 0) { $Position = ([int][Math]::Floor($Position/2048)+1)*2048; continue }
        $NameLength = $Iso[$Position+32]
        if (-not ($Iso[$Position+25] -band 2)) {
            $Files += [pscustomobject]@{name=[Text.Encoding]::ASCII.GetString($Iso,$Position+33,$NameLength); start=[BitConverter]::ToUInt32($Iso,$Position+2)*2048; length=[BitConverter]::ToUInt32($Iso,$Position+10)}
        }
        $Position += $Length
    }
    Assert-Case ($Files.Count -eq 1 -and $Files[0].name -eq 'AUTOUNATTEND.XML;1') 'only root Autounattend.xml on answer ISO'
    $Extracted = [byte[]]::new($Files[0].length)
    [Array]::Copy($Iso,$Files[0].start,$Extracted,0,$Extracted.Length)
    Assert-Case ([Convert]::ToBase64String($Extracted) -eq [Convert]::ToBase64String($XmlBytes)) 'binary ISO answer bytes exactly match XML'
    Assert-Rejected { New-AcceptanceAnswerIso $XmlBytes $IsoPath $TestRoot } 'existing ISO never overwritten'
    Assert-Case (-not (Test-Path -LiteralPath (Join-Path $TestRoot 'Autounattend.xml'))) 'no plaintext answer staging file'

    # An inherited/explicit unrelated reader must be rejected before credential use.
    $BadFile = Join-Path $TestRoot 'bad-acl.xml'
    [IO.File]::WriteAllText($BadFile,'synthetic')
    $BadAcl = Get-Acl -LiteralPath $BadFile
    $BadAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),[Security.AccessControl.FileSystemRights]::Read,[Security.AccessControl.AccessControlType]::Allow))
    Set-Acl -LiteralPath $BadFile -AclObject $BadAcl
    Assert-Rejected { Assert-AcceptancePrivateAcl $BadFile } 'unexpected read access rejected'
    # The one later host-integration exception is exact-VM read on answer.iso.
    # Use an unrelated synthetic SID; no VM or account is created by this test.
    $VmSid = 'S-1-5-83-1234567-7654321-246810-13579'
    $VmAcl = Get-Acl -LiteralPath $IsoPath
    $VmAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($VmSid),[Security.AccessControl.FileSystemRights]::Read,[Security.AccessControl.AccessControlType]::Allow))
    Set-Acl -LiteralPath $IsoPath -AclObject $VmAcl
    Assert-AcceptancePrivateAcl $IsoPath $VmSid
    $script:Passed++
    Assert-Rejected { Assert-AcceptancePrivateAcl $IsoPath } 'VM read grant requires the exact identity exception'
    $VmAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($VmSid),[Security.AccessControl.FileSystemRights]::Write,[Security.AccessControl.AccessControlType]::Allow))
    Set-Acl -LiteralPath $IsoPath -AclObject $VmAcl
    Assert-Rejected { Assert-AcceptancePrivateAcl $IsoPath $VmSid } 'exact VM cannot gain write access'

    $JunctionTarget = Join-Path $TestRoot 'junction-target'
    $JunctionPath = Join-Path $TestRoot 'junction'
    New-Item -ItemType Directory -Path $JunctionTarget | Out-Null
    New-Item -ItemType Junction -Path $JunctionPath -Target $JunctionTarget | Out-Null
    try { Assert-Rejected { Assert-AcceptanceChildPath (Join-Path $JunctionPath 'child') $TestRoot } 'real Windows junction ancestor rejected' }
    finally {
        if ([IO.Path]::GetDirectoryName($JunctionPath) -ne $TestRoot -or -not ((Get-Item -LiteralPath $JunctionPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Unexpected test junction cleanup target.' }
        # Delete only the newly created junction entry, without recursion.
        [IO.Directory]::Delete($JunctionPath)
    }

    # Exercise the complete Prepare/Verify flow on a synthetic workspace. Only
    # the expensive external official-media hash boundary is replaced; package
    # SHA256, native IMAPI, filesystem ACLs and DPAPI remain real.
    $AcceptanceWorkspace = Join-Path $TestRoot 'workspace'
    $AcceptanceVmRoot = Join-Path $AcceptanceWorkspace ('hyperv\' + $AcceptanceVmName)
    $AcceptanceBootstrapRoot = Join-Path $AcceptanceVmRoot 'bootstrap'
    $MediaRoot = Join-Path $AcceptanceWorkspace 'hyperv\media'
    New-Item -ItemType Directory -Path $AcceptanceVmRoot,$MediaRoot | Out-Null
    $InstallationIsoPath = Join-Path $MediaRoot 'synthetic.iso'
    [IO.File]::WriteAllText($InstallationIsoPath,'test-only external media boundary')
    $OwnerFile = Join-Path $AcceptanceVmRoot 'ownership.json'
    $FakeOwner = [ordered]@{name=$AcceptanceVmName; root=$AcceptanceVmRoot; vm_id=[Guid]::NewGuid().ToString(); disk_path=(Join-Path $AcceptanceVmRoot 'disks\Windows11-Acceptance.vhdx'); clean_checkpoint_verified=$false}
    $FakeOwner | ConvertTo-Json | Set-Content -LiteralPath $OwnerFile
    function Get-FileHash {
        param([string]$LiteralPath,[string]$Algorithm)
        if ($LiteralPath -eq $InstallationIsoPath) { return [pscustomobject]@{Hash=$AcceptanceOfficialHash} }
        Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm
    }
    $Action = 'Prepare'; $ImageName = 'Windows 11 Enterprise Evaluation'
    $Prepared = Invoke-AcceptancePreparation
    Assert-Case ($Prepared.state -eq 'prepared_not_attached' -and -not $Prepared.guest_ready) 'full Prepare explicitly stops before attachment'
    Assert-Rejected { Invoke-AcceptancePreparation } 'full Prepare refuses duplicate material'
    $PackagePath = $Prepared.package_path
    $Action = 'Verify'
    Assert-Case (Invoke-AcceptancePreparation).integrity_verified 'full Verify passes real package integrity'
    $Restored = Get-AcceptanceCredential $PackagePath
    Assert-Case ($Restored -is [PSCredential] -and $Restored.UserName -eq 'RedLotusAcceptance') 'credential consumer gets protected in-memory credential'
    $ManifestText = [IO.File]::ReadAllText((Join-Path $PackagePath 'manifest.json'))
    Assert-Case (-not $ManifestText.Contains($Restored.GetNetworkCredential().Password)) 'nonsecret manifest contains no credential'
    $FakeOwner.name = 'Existing-Unrelated-VM'
    $FakeOwner | ConvertTo-Json | Set-Content -LiteralPath $OwnerFile
    Assert-Rejected { Get-AcceptanceCredential $PackagePath } 'changed ownership rejected before credential release'
    $FakeOwner.name = $AcceptanceVmName
    $FakeOwner | ConvertTo-Json | Set-Content -LiteralPath $OwnerFile
    [IO.File]::AppendAllText((Join-Path $PackagePath 'answer.iso'),'tamper')
    Assert-Rejected { Get-AcceptanceCredential $PackagePath } 'changed answer ISO rejected before credential release'
    Write-Output ("PASS: {0} unattended preparation cases; no VM or disk actions." -f $Passed)
} finally {
    $Credential = $null; $Recovered = $null; $Secret = $null; $Answer = $null
    if ($XmlBytes) { [Array]::Clear($XmlBytes) }
    # This directory was created above by this test. Resolve and reject reparses
    # before deleting only these synthetic fixtures, never a real prep package.
    $VerifiedRoot = Assert-AcceptanceChildPath $TestRoot $TestParent
    if ([IO.Path]::GetFileName($VerifiedRoot) -notmatch '^unattend-test-[0-9a-f]{32}$') { throw 'Unsafe test cleanup path.' }
    foreach ($Entry in Get-ChildItem -LiteralPath $VerifiedRoot -Recurse -Force) { Assert-AcceptanceChildPath $Entry.FullName $VerifiedRoot | Out-Null }
    Remove-Item -LiteralPath $VerifiedRoot -Recurse -Force
}
