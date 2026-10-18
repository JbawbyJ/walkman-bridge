# Deterministic process-ownership regressions. All OS process calls are replaced;
# this script never starts or terminates a real process.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'smoke-install.ps1')

$started = [datetime]'2026-10-18T12:00:00Z'
$ownedPath = 'C:\smoke-test-owned-run'
$script:Snapshot = @()
$script:Stopped = [System.Collections.Generic.List[int]]::new()
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    if ($ClassName -ne 'Win32_Process') { throw "Unexpected process query: $ClassName" }
    if ($Filter) {
        if ($Filter -notmatch '^ProcessId=(\d+)$') { throw "Unexpected filter: $Filter" }
        return @($script:Snapshot | Where-Object { [int]$_.ProcessId -eq [int]$Matches[1] })
    }
    return $script:Snapshot
}
function Stop-Process {
    param([int]$Id, [switch]$Force, $ErrorAction)
    throw 'Cleanup must never terminate through a fresh PID lookup'
}
function Open-SmokeProcessHandle {
    param([int]$ProcessId)
    $row = @($script:Snapshot | Where-Object { [int]$_.ProcessId -eq $ProcessId })[0]
    if (-not $row) { throw 'Fixture process is no longer running' }
    $handle = [pscustomobject]@{ CreationTimeUtc = $row.CreationDate; ProcessId = $ProcessId }
    $handle | Add-Member -MemberType ScriptMethod -Name Terminate -Value { [void]$script:Stopped.Add($this.ProcessId) }
    $handle | Add-Member -MemberType ScriptMethod -Name Dispose -Value {}
    return $handle
}
function ProcessRow([int]$Id, [int]$Parent, $Created, [string]$Path = 'C:\unrelated\app.exe') {
    [pscustomobject]@{ Name = 'fixture.exe'; ProcessId = $Id; ParentProcessId = $Parent;
        CommandLine = $Path; ExecutablePath = $Path; CreationDate = $Created }
}
function Assert-Ids($Rows, [int[]]$Expected, [string]$Label) {
    $actual = (@($Rows | ForEach-Object { [int]$_.ProcessId }) | Sort-Object) -join ','
    $wanted = (@($Expected) | Sort-Object) -join ','
    if ($actual -ne $wanted) { throw "${Label}: expected [$wanted], got [$actual]" }
}
function TrackedRoot([bool]$Exited = $true) {
    $tracked = [pscustomobject]@{ Id = 32100; HasExited = $Exited; StartTime = $started }
    $tracked | Add-Member -MemberType ScriptMethod -Name Refresh -Value {}
    return $tracked
}

# The launch PID now belongs to a different tree. Only the independently
# identified helper beneath the unique run path belongs to this smoke run.
$script:Snapshot = @(
    (ProcessRow 32100 1 $started.AddSeconds(60)),
    (ProcessRow 32101 32100 $started.AddSeconds(61)),
    (ProcessRow 32102 999 $started.AddSeconds(1) "$ownedPath\python.exe")
)
$found = @(Get-SmokeProcesses -RootProcessId 32100 -OwnedPath $ownedPath -SmokeStart $started)
Assert-Ids $found @(32102) 'Reused root must not donate an unrelated process tree'
Stop-SmokeLeftovers -Processes $found -Process (TrackedRoot) -SmokeStart $started
if (($script:Stopped -join ',') -ne '32102') { throw 'Cleanup terminated a process outside the owned run' }

# A stale caller must not turn a freshly copied replacement-root row into
# ownership proof at the final termination boundary either.
$script:Stopped.Clear()
Stop-SmokeLeftovers -Processes @($script:Snapshot[0]) -Process (TrackedRoot) -SmokeStart $started
if ($script:Stopped.Count) { throw 'Cleanup terminated a replacement root PID' }

# Process.StartTime and CIM differ by sub-millisecond rounding on Windows.
$script:Snapshot = @(
    (ProcessRow 32100 1 $started.AddTicks(-5000)),
    (ProcessRow 32101 32100 $started.AddSeconds(1))
)
Assert-Ids @(Get-SmokeProcesses -RootProcessId 32100 -OwnedPath $ownedPath -SmokeStart $started) @(32100,32101) 'Original root and child remain discoverable'

# Missing timestamps cannot authenticate the root or justify killing a PID.
$script:Snapshot = @((ProcessRow 32100 1 $null), (ProcessRow 32101 32100 $started.AddSeconds(1)))
Assert-Ids @(Get-SmokeProcesses -RootProcessId 32100 -OwnedPath $ownedPath -SmokeStart $started) @() 'Unknown root identity must not admit descendants'
$script:Stopped.Clear()
Stop-SmokeLeftovers -Processes @($script:Snapshot[0]) -Process (TrackedRoot) -SmokeStart $started
if ($script:Stopped.Count) { throw 'Cleanup terminated a root with unknown creation time' }

$script:Snapshot = @((ProcessRow 32100 1 $started.AddSeconds(60)))
$rejected = $false
try { Get-SmokeProcesses -RootProcessId 32100 -OwnedPath $ownedPath -SmokeStart $null | Out-Null }
catch { $rejected = $true }
if (-not $rejected) { throw 'Discovery substituted the current PID occupant for missing launch identity' }

# The PID changes occupants between validation and termination. A pinned handle
# must continue to identify the checked process, never reopen its numeric PID.
$script:Original = ProcessRow 32101 32100 $started.AddSeconds(1)
$script:Replacement = ProcessRow 32101 999 $started.AddSeconds(90)
$script:CurrentOccupant = $script:Original
$script:Terminated = [System.Collections.Generic.List[object]]::new()
$script:Disposed = 0
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    $checked = $script:CurrentOccupant
    $script:CurrentOccupant = $script:Replacement
    return $checked
}
function Stop-Process {
    param([int]$Id, [switch]$Force, $ErrorAction)
    [void]$script:Terminated.Add($script:CurrentOccupant)
}
function Open-SmokeProcessHandle {
    param([int]$ProcessId)
    $opened = $script:CurrentOccupant
    $script:CurrentOccupant = $script:Replacement
    $handle = [pscustomobject]@{ CreationTimeUtc = $opened.CreationDate; Target = $opened }
    $handle | Add-Member -MemberType ScriptMethod -Name Terminate -Value { [void]$script:Terminated.Add($this.Target) }
    $handle | Add-Member -MemberType ScriptMethod -Name Dispose -Value { $script:Disposed++ }
    return $handle
}
Stop-SmokeLeftovers -Processes @($script:Original) -Process $null -SmokeStart $started
if ($script:Terminated.Count -ne 1 -or -not [object]::ReferenceEquals($script:Terminated[0], $script:Original)) {
    throw 'Cleanup reopened a numeric PID after checking its identity and terminated the replacement'
}
if ($script:Disposed -ne 1) { throw 'Cleanup did not release its pinned process handle' }

# A replacement already present when OpenProcess runs fails identity validation,
# and even rejected handles must be disposed.
$script:Terminated.Clear()
$script:Disposed = 0
$script:CurrentOccupant = $script:Replacement
Stop-SmokeLeftovers -Processes @($script:Original) -Process $null -SmokeStart $started
if ($script:Terminated.Count -ne 0 -or $script:Disposed -ne 1) { throw 'Replacement handle was not rejected and disposed' }

# Win32_Process timestamps have microsecond precision; native FILETIME can have
# a sub-microsecond remainder. It still identifies the original process.
$script:CurrentOccupant = ProcessRow 32101 32100 $script:Original.CreationDate.AddTicks(7)
$script:Terminated.Clear()
Stop-SmokeLeftovers -Processes @($script:Original) -Process $null -SmokeStart $started
if ($script:Terminated.Count -ne 1) { throw 'CIM timestamp precision rejected the original native process handle' }

# Opening, reading identity, and terminating may fail independently. None may
# fall back to PID termination, and every opened handle must be released.
foreach ($failure in @('open', 'identity', 'terminate')) {
    $script:FailurePoint = $failure
    $script:Disposed = 0
    $script:Terminated.Clear()
    function Open-SmokeProcessHandle {
        param([int]$ProcessId)
        if ($script:FailurePoint -eq 'open') { throw 'Access denied (fixture)' }
        $handle = [pscustomobject]@{}
        $handle | Add-Member -MemberType ScriptProperty -Name CreationTimeUtc -Value {
            if ($script:FailurePoint -eq 'identity') { throw 'Cannot query process (fixture)' }
            return $script:Original.CreationDate
        }
        $handle | Add-Member -MemberType ScriptMethod -Name Terminate -Value { throw 'Cannot terminate process (fixture)' }
        $handle | Add-Member -MemberType ScriptMethod -Name Dispose -Value { $script:Disposed++ }
        return $handle
    }
    Stop-SmokeLeftovers -Processes @($script:Original) -Process $null -SmokeStart $started
    $expectedDisposals = if ($failure -eq 'open') { 0 } else { 1 }
    if ($script:Terminated.Count -ne 0 -or $script:Disposed -ne $expectedDisposals) {
        throw "Failure at $failure leaked a handle or fell back to a PID lookup"
    }
}

Write-Host 'Smoke ownership regressions passed (mock process APIs only).'
