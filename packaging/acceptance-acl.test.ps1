#requires -Version 7.2
<# Tests only. All ACLs belong to new synthetic files in a private test directory.
   No real package, VM, credentials, ISO mount, registry or host policy is accessed.
   The positive capability case is deliberately RED against the prior validator. #>
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$Source=Join-Path $PSScriptRoot 'prepare-acceptance-unattend.ps1'
$SourceHash=(Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
$Tokens=$null; $Errors=$null
$Ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$Tokens,[ref]$Errors)
if ($Errors.Count) { throw 'Preparation source parse failed.' }
foreach ($Name in @('Assert-AcceptanceChildPath','Assert-AcceptancePrivateAcl','New-AcceptancePrivateDirectory')) {
    $Definition=$Ast.Find({param($Node) $Node -is [Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -eq $Name},$false)
    if (-not $Definition) { throw "Missing reviewed function: $Name" }
    . ([scriptblock]::Create($Definition.Extent.Text))
}
$VmSid='S-1-5-83-1234567-7654321-246810-13579'
$OtherVmSid='S-1-5-83-9876543-1234567-246810-13579'
# Exact capability observed independently in the Hyper-V attachment postflight.
# This value is intentionally fixed in the test rather than imported from source.
$CapabilitySid='S-1-15-3-1024-2268835264-3721307629-241982045-173645152-1490879176-104643441-2915960892-1612460704'
$OtherCapabilitySid='S-1-15-3-1024-2268835264-3721307629-241982045-173645152-1490879176-104643441-2915960892-1612460705'
$Read=[Security.AccessControl.FileSystemRights]::Read -bor [Security.AccessControl.FileSystemRights]::Synchronize
$VmRead=$Read
$VmCompatibilityRead=[Security.AccessControl.FileSystemRights]::ReadAndExecute -bor [Security.AccessControl.FileSystemRights]::Synchronize
$Results=[Collections.Generic.List[object]]::new()
$TestRoot=Join-Path (Join-Path $PSScriptRoot 'build') ('acceptance-acl-review-'+[Guid]::NewGuid().ToString('N'))
New-AcceptancePrivateDirectory $TestRoot | Out-Null

function Assert-True([bool]$Value,[string]$Message) { if (-not $Value) { throw $Message } }
function Test-Case([string]$Name,[scriptblock]$Body) {
    try { & $Body; $script:Results.Add([pscustomobject]@{name=$Name;passed=$true}) }
    catch { $script:Results.Add([pscustomobject]@{name=$Name;passed=$false;error=$_.Exception.Message}) }
}
function Assert-Rejected([scriptblock]$Body) {
    $Rejected=$false
    try { & $Body | Out-Null } catch { $Rejected=$true }
    Assert-True $Rejected 'Validator accepted an out-of-contract ACL.'
}
function New-SyntheticFile([string]$Name='answer.iso') {
    $Parent=Join-Path $TestRoot ([Guid]::NewGuid().ToString('N'))
    New-AcceptancePrivateDirectory $Parent | Out-Null
    $Path=Join-Path $Parent $Name
    [IO.File]::WriteAllText($Path,'Synthetic harmless ACL fixture. Not an ISO or credential.')
    return $Path
}
function Add-SyntheticAce([string]$Path,[string]$Sid,[Security.AccessControl.FileSystemRights]$Rights,
    [Security.AccessControl.InheritanceFlags]$Inheritance=[Security.AccessControl.InheritanceFlags]::None,
    [Security.AccessControl.AccessControlType]$Type=[Security.AccessControl.AccessControlType]::Allow) {
    # Persist only the DACL section. Set-Acl on a directory can attempt unrelated
    # security sections and require SeSecurityPrivilege in a restricted process.
    $IsDirectory=(Get-Item -LiteralPath $Path -Force).PSIsContainer
    $Info=if ($IsDirectory) { [IO.DirectoryInfo]::new($Path) } else { [IO.FileInfo]::new($Path) }
    $Acl=[IO.FileSystemAclExtensions]::GetAccessControl($Info,[Security.AccessControl.AccessControlSections]::Access)
    $Rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($Sid),$Rights,
        $Inheritance,[Security.AccessControl.PropagationFlags]::None,$Type)
    $Acl.AddAccessRule($Rule)
    [IO.FileSystemAclExtensions]::SetAccessControl($Info,$Acl)
}
function Assert-Ace([string]$Path,[string]$Sid,[bool]$Inherited,[Security.AccessControl.FileSystemRights]$Rights) {
    $Rules=@((Get-Acl -LiteralPath $Path).GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | Where-Object {$_.IdentityReference.Value -eq $Sid -and $_.AccessControlType -eq 'Allow'})
    Assert-True ($Rules.Count -eq 1) 'Synthetic ACE was not applied exactly once.'
    Assert-True ($Rules[0].IsInherited -eq $Inherited -and $Rules[0].FileSystemRights -eq $Rights) 'Synthetic ACE differs from the expected observed mask or inheritance.'
}

try {
    Test-Case 'Owner and SYSTEM baseline remains private' {
        $Path=New-SyntheticFile
        Assert-AcceptancePrivateAcl $Path
    }
    Test-Case 'Existing exact VM read-and-execute exception remains valid' {
        $Path=New-SyntheticFile
        Add-SyntheticAce $Path $VmSid $VmCompatibilityRead
        Assert-Ace $Path $VmSid $false $VmCompatibilityRead
        Assert-AcceptancePrivateAcl $Path $VmSid
    }
    Test-Case 'Explicit exact VM Read/Synchronize plus fixed capability Read/Synchronize is accepted on answer.iso' {
        $Path=New-SyntheticFile
        Add-SyntheticAce $Path $VmSid $VmRead
        Add-SyntheticAce $Path $CapabilitySid $Read
        Assert-Ace $Path $VmSid $false $VmRead
        Assert-Ace $Path $CapabilitySid $false $Read
        Assert-AcceptancePrivateAcl $Path $VmSid
    }
    Test-Case 'Observed legacy VM execute permission is rejected when capability is present' {
        $Path=New-SyntheticFile
        Add-SyntheticAce $Path $VmSid $VmCompatibilityRead
        Add-SyntheticAce $Path $CapabilitySid $Read
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
    }
    Test-Case 'Capability without paired VM ACE is rejected even when expected SID is supplied' {
        $Path=New-SyntheticFile
        Add-SyntheticAce $Path $CapabilitySid $Read
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
    }
    Test-Case 'Capability and VM ACE without supplied expected identity are rejected' {
        $Path=New-SyntheticFile
        Add-SyntheticAce $Path $VmSid $VmRead
        Add-SyntheticAce $Path $CapabilitySid $Read
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path }
    }
    Test-Case 'Capability paired with another VM SID is rejected' {
        $Path=New-SyntheticFile
        Add-SyntheticAce $Path $OtherVmSid $VmRead
        Add-SyntheticAce $Path $CapabilitySid $Read
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
    }
    Test-Case 'Unknown capability is rejected even with the exact VM' {
        $Path=New-SyntheticFile
        Add-SyntheticAce $Path $VmSid $VmRead
        Add-SyntheticAce $Path $OtherCapabilitySid $Read
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
    }
    foreach ($Name in @('credential.xml','manifest.json','other.iso')) {
        Test-Case "Capability exception is forbidden on $Name" {
            $Path=New-SyntheticFile $Name
            Add-SyntheticAce $Path $VmSid $VmRead
            Add-SyntheticAce $Path $CapabilitySid $Read
            Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
        }
    }
    Test-Case 'Capability exception is forbidden on a directory named answer.iso' {
        $Parent=Join-Path $TestRoot ([Guid]::NewGuid().ToString('N'))
        New-AcceptancePrivateDirectory $Parent | Out-Null
        $Path=Join-Path $Parent 'answer.iso'
        New-AcceptancePrivateDirectory $Path | Out-Null
        Add-SyntheticAce $Path $VmSid $VmRead
        Add-SyntheticAce $Path $CapabilitySid $Read
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
    }
    foreach ($Extra in @('Write','ExecuteFile','Delete','ChangePermissions','TakeOwnership','FullControl')) {
        Test-Case "Capability with additional $Extra rights is rejected" {
            $Path=New-SyntheticFile
            Add-SyntheticAce $Path $VmSid $VmRead
            Add-SyntheticAce $Path $CapabilitySid ($Read -bor [Security.AccessControl.FileSystemRights]$Extra)
            Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
        }
    }
    Test-Case 'Capability with actual inherited ACE is rejected' {
        $Path=New-SyntheticFile
        $Parent=[IO.Path]::GetDirectoryName($Path)
        Add-SyntheticAce $Parent $CapabilitySid $Read ([Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit')
        Add-SyntheticAce $Path $VmSid $VmRead
        Assert-Ace $Path $CapabilitySid $true $Read
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
    }
    Test-Case 'Capability cannot pair with an inherited VM ACE' {
        $Path=New-SyntheticFile
        $Parent=[IO.Path]::GetDirectoryName($Path)
        Add-SyntheticAce $Parent $VmSid $VmRead ([Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit')
        Add-SyntheticAce $Path $CapabilitySid $Read
        Assert-Ace $Path $VmSid $true $VmRead
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
    }
    foreach ($InheritedRights in @($VmRead,$VmCompatibilityRead)) {
        Test-Case "Valid explicit VM reader does not hide an inherited VM grant ($InheritedRights)" {
            $Path=New-SyntheticFile
            $Parent=[IO.Path]::GetDirectoryName($Path)
            Add-SyntheticAce $Parent $VmSid $InheritedRights ([Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit')
            Add-SyntheticAce $Path $VmSid $VmRead
            Add-SyntheticAce $Path $CapabilitySid $Read
            $VmRules=@((Get-Acl -LiteralPath $Path).GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | Where-Object {$_.IdentityReference.Value -eq $VmSid -and $_.AccessControlType -eq 'Allow'})
            Assert-True ($VmRules.Count -eq 2 -and @($VmRules | Where-Object IsInherited).Count -eq 1) 'Mixed explicit/inherited fixture was not created.'
            Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
        }
    }
    Test-Case 'Capability mask must exactly match Read/Synchronize' {
        $Path=New-SyntheticFile
        Add-SyntheticAce $Path $VmSid $VmRead
        Add-SyntheticAce $Path $CapabilitySid ([Security.AccessControl.FileSystemRights]::ReadAttributes -bor [Security.AccessControl.FileSystemRights]::Synchronize)
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
    }
    Test-Case 'Paired VM mask must exactly match Read/Synchronize when capability is present' {
        $Path=New-SyntheticFile
        Add-SyntheticAce $Path $VmSid ([Security.AccessControl.FileSystemRights]::ReadAttributes -bor [Security.AccessControl.FileSystemRights]::Synchronize)
        Add-SyntheticAce $Path $CapabilitySid $Read
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
    }
    Test-Case 'Capability cannot pair with only a VM Deny ACE' {
        $Path=New-SyntheticFile
        Add-SyntheticAce $Path $VmSid $VmRead -Type Deny
        Add-SyntheticAce $Path $CapabilitySid $Read
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
    }
    Test-Case 'Excessive VM write rights remain rejected beside allowed capability rights' {
        $Path=New-SyntheticFile
        Add-SyntheticAce $Path $VmSid ($VmRead -bor [Security.AccessControl.FileSystemRights]::Write)
        Add-SyntheticAce $Path $CapabilitySid $Read
        Assert-Rejected { Assert-AcceptancePrivateAcl $Path $VmSid }
    }
} finally {
    $AbsoluteRoot=[IO.Path]::GetFullPath($TestRoot)
    $ExpectedParent=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'build'))
    if ([IO.Path]::GetDirectoryName($AbsoluteRoot) -ne $ExpectedParent -or [IO.Path]::GetFileName($AbsoluteRoot) -notmatch '^acceptance-acl-review-[a-f0-9]{32}$') { throw 'Unsafe synthetic cleanup target.' }
    if ((Get-Item -LiteralPath $AbsoluteRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Synthetic root unexpectedly became a reparse point.' }
    Remove-Item -LiteralPath $AbsoluteRoot -Recurse -Force
}
$Report=[ordered]@{source_sha256=$SourceHash;passed=@($Results | Where-Object passed).Count;failed=@($Results | Where-Object {-not $_.passed}).Count;tests=$Results;scope='Synthetic private ACL fixtures only. No real package, credentials or VM accessed.'}
$Report | ConvertTo-Json -Depth 6
if ($Report.failed) { exit 1 }
