# Exercises the installed-smoke hang path without an installer.
# A hung parent and its child must be killed, diagnostics must redact paths,
# the cleanup result must fail, and an older unrelated sleeper must survive.
# A second case exits the parent and leaves the child under the run directory.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'smoke-install.ps1')
if ($ShutdownGraceSeconds -ne 45) { throw "Installed smoke grace is $ShutdownGraceSeconds, expected 45" }

function Assert-SmokeRedaction {
    param([string]$InputText, [string]$Expected)
    $actual = Hide-SmokePath $InputText
    if ($actual -ne $Expected) { throw "Redaction mismatch.`n input: $InputText`n actual: $actual`n expected: $Expected" }
}
function Format-HangCapture($Captured) {
    (($Captured | ForEach-Object {
        if ($_ -is [System.Management.Automation.InformationRecord]) { [string]$_.MessageData }
        elseif ($_ -is [System.Management.Automation.ErrorRecord]) { [string]$_.Exception.Message }
        else { [string]$_ }
    }) -join "`n")
}
$savedRepo = $Repo
$savedProfile = $env:USERPROFILE
$proofPath = $null
try {
    $env:USERPROFILE = 'C:\Users\runner'
    $Repo = 'C:\Users\runner\walkman-bridge'
    Assert-SmokeRedaction 'C:\Users\runner next' '<USERPROFILE> next'
    Assert-SmokeRedaction 'C:\Users\runner"next' '<USERPROFILE>"next'
    Assert-SmokeRedaction "C:\Users\runner'next" "<USERPROFILE>'next"
    Assert-SmokeRedaction 'C:\Users\runner\next' '<USERPROFILE>\next'
    Assert-SmokeRedaction 'C:/Users/runner/next' '<USERPROFILE>/next'
    Assert-SmokeRedaction 'C:\Users\runner' '<USERPROFILE>'
    Assert-SmokeRedaction 'C:\Users\runner.' '<USERPROFILE>.'
    Assert-SmokeRedaction 'C:\Users\runner\walkman-bridge next' '<REPO> next'
    Assert-SmokeRedaction 'C:\Users\runner\walkman-bridge"next' '<REPO>"next'
    Assert-SmokeRedaction "C:\Users\runner\walkman-bridge'next" "<REPO>'next"
    Assert-SmokeRedaction 'C:\Users\runner\walkman-bridge\next' '<REPO>\next'
    Assert-SmokeRedaction 'C:/Users/runner/walkman-bridge/next' '<REPO>/next'
    Assert-SmokeRedaction 'C:\Users\runner\walkman-bridge' '<REPO>'
    Assert-SmokeRedaction 'C:\Users\runner\walkman-bridge.' '<REPO>.'
    Assert-SmokeRedaction 'C:\Users\runneradmin\keep' 'C:\Users\runneradmin\keep'
    Assert-SmokeRedaction 'C:\Users\runner\walkman-bridge\packaging C:\Users\runner\AppData' '<REPO>\packaging <USERPROFILE>\AppData'
    $env:USERPROFILE = 'C:\Users\runneradmin'
    $Repo = 'C:\Users\runneradmin\walkman-bridge'
    Assert-SmokeRedaction 'c:\users\runneradmin\x' '<USERPROFILE>\x'
    Assert-SmokeRedaction 'C:/Users/runneradmin/docs' '<USERPROFILE>/docs'
    Assert-SmokeRedaction 'C:/Users/runneradmin/walkman-bridge/src' '<REPO>/src'
    Assert-SmokeRedaction 'C:\Users/runneradmin\x' '<USERPROFILE>\x'
    Assert-SmokeRedaction 'C:\Users/runneradmin\walkman-bridge/src' '<REPO>/src'
    Assert-SmokeRedaction 'C:\Users\runneradmin next' '<USERPROFILE> next'
    Assert-SmokeRedaction 'C:\Users\runneradmin"next' '<USERPROFILE>"next'
    Assert-SmokeRedaction "C:\Users\runneradmin'next" "<USERPROFILE>'next"
    Assert-SmokeRedaction 'C:\Users\runneradmin;next' '<USERPROFILE>;next'
    Assert-SmokeRedaction 'C:\Users\runneradmin,next' '<USERPROFILE>,next'
    Assert-SmokeRedaction 'C:\Users\runneradmin)next' '<USERPROFILE>)next'
    Assert-SmokeRedaction 'C:\Users\runneradmin=next' '<USERPROFILE>=next'
    Assert-SmokeRedaction 'C:\Users\runneradmin\next' '<USERPROFILE>\next'
    Assert-SmokeRedaction 'C:/Users/runneradmin/next' '<USERPROFILE>/next'
    Assert-SmokeRedaction 'C:\Users\runneradmin' '<USERPROFILE>'
    Assert-SmokeRedaction 'Finished at C:\Users\runneradmin.' 'Finished at <USERPROFILE>.'
    Assert-SmokeRedaction 'Finished at C:\Users\runneradmin. Next' 'Finished at <USERPROFILE>. Next'
    Assert-SmokeRedaction 'C:\Users\runneradmin.txt' 'C:\Users\runneradmin.txt'
    Assert-SmokeRedaction 'C:\\Users\\runneradmin\\walkman-bridge\\src' '<REPO>\\src'
    Assert-SmokeRedaction 'C:\\Users\\runneradmin\\AppData' '<USERPROFILE>\\AppData'
    $script:SmokeShortPathOverride = @{ 'C:\Users\runneradmin' = 'C:\Users\runneradmin' }
    Assert-SmokeRedaction 'C:\Users\RUNNER~1\keep' 'C:\Users\RUNNER~1\keep'
    $script:SmokeShortPathOverride = @{
        'C:\Users\runneradmin' = 'C:\Users\RUNNER~1'
        'C:\Users\runneradmin\walkman-bridge' = 'C:\Users\RUNNER~1\WALKMA~1'
    }
    Assert-SmokeRedaction 'C:\Users\RUNNER~1\AppData' '<USERPROFILE>\AppData'
    Assert-SmokeRedaction 'C:\Users\RUNNER~1\WALKMA~1\src' '<REPO>\src'
    Assert-SmokeRedaction 'C:\\Users\\RUNNER~1\\AppData' '<USERPROFILE>\\AppData'
    $proof = [ordered]@{
        product = 'player'
        installer = 'C:\Users\runneradmin\walkman-bridge\RedLotusPlayer-Setup.exe'
        installed_path = 'C:\Users\runneradmin\walkman-bridge\installed'
        profile_note = 'C:\Users\runneradmin\AppData'
        passed = $true
    }
    $redactedProof = Hide-SmokePath ($proof | ConvertTo-Json -Depth 5)
    $proofPath = Join-Path ([IO.Path]::GetTempPath()) ("smoke-result-" + [guid]::NewGuid().ToString('N') + '.json')
    Set-Content -LiteralPath $proofPath -Encoding utf8 -Value $redactedProof
    if ($redactedProof -match 'runneradmin' -or $redactedProof -match 'RUNNER~1') { throw "result.json leaked a profile or short path: $redactedProof" }
    $parsed = Get-Content -LiteralPath $proofPath -Raw | ConvertFrom-Json
    if ($parsed.product -ne 'player' -or $parsed.passed -ne $true) { throw "result.json lost its fields: $redactedProof" }
    if ($parsed.installer -ne '<REPO>\RedLotusPlayer-Setup.exe') { throw "result.json installer: $($parsed.installer)" }
    if ($parsed.installed_path -ne '<REPO>\installed') { throw "result.json installed_path: $($parsed.installed_path)" }
    if ($parsed.profile_note -ne '<USERPROFILE>\AppData') { throw "result.json profile_note: $($parsed.profile_note)" }
    $runLine = 'C:\Users\runneradmin\walkman-bridge\packaging\build\install-smoke\player-1'
    $passedLine = Hide-SmokePath "Installed smoke passed: player ($runLine)"
    if ($passedLine -ne 'Installed smoke passed: player (<REPO>\packaging\build\install-smoke\player-1)') { throw "success line: $passedLine" }
    $script:SmokeShortPathOverride = $null
    $unusedShort = Get-SmokeShortPath 'C:\no\such\smoke-short\path'
    if ($unusedShort) { throw "missing folder returned a short path: $unusedShort" }
} finally {
    $Repo = $savedRepo
    $script:SmokeShortPathOverride = $null
    if ($null -eq $savedProfile) { Remove-Item Env:USERPROFILE -ErrorAction SilentlyContinue } else { $env:USERPROFILE = $savedProfile }
    if ($proofPath -and (Test-Path -LiteralPath $proofPath)) { Remove-Item -LiteralPath $proofPath -Force -ErrorAction SilentlyContinue }
}
Write-Host 'Redaction cases passed: space, double quote, single quote, semicolon, comma, close paren, equals, backslash, forward slash, end of string, sentence-final dot, runner versus runneradmin, case, mixed slash, longer path, 8.3 short path, JSON escapes, result.json, success line.'

if (-not (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)) {
    Write-Host 'Win32_Process/CIM is not available; hang cleanup cases were not exercised here.'
    return
}

$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwsh) { $pwsh = (Get-Command powershell.exe -ErrorAction Stop).Source }
function Start-HangProcess([string[]]$ArgumentList) {
    $start = @{ FilePath = $pwsh; ArgumentList = $ArgumentList; PassThru = $true }
    if ($IsWindows) { $start.WindowStyle = 'Hidden' }
    Start-Process @start
}
$Run = Join-Path $Repo ("packaging\build\hang-selftest\" + [guid]::NewGuid().ToString('N'))
$scratch = Join-Path ([IO.Path]::GetTempPath()) ("smoke-unrelated-" + [guid]::NewGuid().ToString('N'))
$unrelated = $null
$parent = $null
$exitParent = $null
$childId = 0
$childStart = $null
$lingerId = 0
$lingerStart = $null
try {
    New-Item -ItemType Directory -Force -Path $scratch,$Run | Out-Null
    $unrelatedScript = Join-Path $scratch 'sleep.ps1'
    Set-Content -LiteralPath $unrelatedScript -Encoding utf8 "while (`$true) { Start-Sleep -Seconds 30 }`n"
    $unrelated = Start-HangProcess @('-NoProfile','-File', $unrelatedScript)
    Start-Sleep -Seconds 2
    $childScript = Join-Path $Run 'child.ps1'
    $parentScript = Join-Path $Run 'parent.ps1'
    $childPidFile = Join-Path $Run 'child.pid'
    Set-Content -LiteralPath $childScript -Encoding utf8 "while (`$true) { Start-Sleep -Seconds 30 }`n"
    @"
`$childStart = @{ FilePath = "$pwsh"; ArgumentList = @('-NoProfile','-File', "$childScript", "$env:USERPROFILE", "$Repo"); PassThru = `$true }
if (`$IsWindows) { `$childStart.WindowStyle = 'Hidden' }
`$child = Start-Process @childStart
Set-Content -LiteralPath "$childPidFile" -Value `$child.Id
while (`$true) { Start-Sleep -Seconds 30 }
"@ | Set-Content -LiteralPath $parentScript -Encoding utf8
    $parent = Start-HangProcess @('-NoProfile','-File', $parentScript, $env:USERPROFILE, $Repo)
    try { $parentStart = $parent.StartTime.ToUniversalTime() } catch { $parentStart = $null }
    $childText = ''
    $deadline = [datetime]::UtcNow.AddSeconds(15)
    do {
        if (Test-Path -LiteralPath $childPidFile) {
            $childText = (Get-Content -LiteralPath $childPidFile -Raw -ErrorAction SilentlyContinue)
            if ($childText) { $childText = $childText.Trim() }
            if ($childText) { break }
        }
        if ([datetime]::UtcNow -ge $deadline) { throw "Hung child did not start; parent $($parent.Id)" }
        Start-Sleep -Seconds 1
    } while ($true)
    $childId = [int]$childText
    try { $childStart = (Get-Process -Id $childId -ErrorAction Stop).StartTime.ToUniversalTime() } catch { $childStart = $null }
    $Log = Join-Path $Run 'redlotus.log'
    @("log $Repo", "log $env:USERPROFILE") | Set-Content -LiteralPath $Log -Encoding utf8
    $Executable = 'hang-parent'
    $script:DiagnosticsWritten = $false
    $script:HangRecorded = $false
    $script:SmokeGraceSeconds = $null
    $captured = & {
        try {
            $cleared = Wait-SmokeShutdown -Process $parent -OwnedPath $Run -GraceSeconds 2 -SmokeStart $parentStart
            if ($cleared) { throw 'Hang cleared during grace; the fake product was expected to stay running.' }
            Publish-SmokeHang -Process $parent -SmokeStart $parentStart
            'HANG_RESULT pass'
        } catch {
            "HANG_RESULT fail: $($_.Exception.Message)"
        }
    } *>&1
    $text = Format-HangCapture $captured
    Write-Host $text
    try { $parent.Refresh() } catch { }
    try { $unrelated.Refresh() } catch { }
    $child = Get-Process -Id $childId -ErrorAction SilentlyContinue
    if (-not $parent.HasExited) { throw "Parent $($parent.Id) was not killed" }
    if ($child -and -not $child.HasExited) { throw "Child $childId was not killed" }
    if ($unrelated.HasExited) { throw "Unrelated process $($unrelated.Id) was killed" }
    foreach ($id in @($parent.Id, $childId)) {
        $lines = @($text -split "`n" | Where-Object { $_ -match "pid=$id parent=" })
        if (-not $lines) { throw "Diagnostics omitted pid $id`n$text" }
        $joined = $lines -join "`n"
        if ($joined -notlike '*<USERPROFILE>*' -or $joined -notlike '*<REPO>*') { throw "Diagnostics for pid $id did not redact USERPROFILE and REPO: $joined" }
    }
    if ($text.Contains($Repo) -or ($env:USERPROFILE -and $text.Contains($env:USERPROFILE))) { throw 'Diagnostics printed a raw profile or repo path' }
    if ($text -notlike '*HANG_RESULT fail:*' -or $text -like '*HANG_RESULT pass*') { throw "Hang cleanup was treated as a pass:`n$text" }
    if ($text -notlike '*after 2s shutdown grace*') { throw "Hang message did not report the 2s grace:`n$text" }
    if ($ShutdownGraceSeconds -ne 45) { throw "Installed smoke grace changed from 45 to $ShutdownGraceSeconds" }
    Write-Host "Hang cleanup test passed: killed parent $($parent.Id) and child $childId; left unrelated $($unrelated.Id) running."

    $lingerScript = Join-Path $Run 'linger.ps1'
    $exitParentScript = Join-Path $Run 'exit-parent.ps1'
    $lingerPidFile = Join-Path $Run 'linger.pid'
    Set-Content -LiteralPath $lingerScript -Encoding utf8 "while (`$true) { Start-Sleep -Seconds 30 }`n"
    @"
`$childStart = @{ FilePath = "$pwsh"; ArgumentList = @('-NoProfile','-File', "$lingerScript", "$Run"); PassThru = `$true }
if (`$IsWindows) { `$childStart.WindowStyle = 'Hidden' }
`$child = Start-Process @childStart
Set-Content -LiteralPath "$lingerPidFile" -Value `$child.Id
"@ | Set-Content -LiteralPath $exitParentScript -Encoding utf8
    $exitParent = Start-HangProcess @('-NoProfile','-File', $exitParentScript)
    try { $lingerSmokeStart = $exitParent.StartTime.ToUniversalTime() } catch { $lingerSmokeStart = $null }
    if (-not $lingerSmokeStart) { throw 'Could not record the linger smoke start time' }
    $lingerText = ''
    $deadline = [datetime]::UtcNow.AddSeconds(20)
    do {
        try { $exitParent.Refresh() } catch { }
        if (Test-Path -LiteralPath $lingerPidFile) {
            $lingerText = (Get-Content -LiteralPath $lingerPidFile -Raw -ErrorAction SilentlyContinue)
            if ($lingerText) { $lingerText = $lingerText.Trim() }
        }
        if ($lingerText -and $exitParent.HasExited) { break }
        if ([datetime]::UtcNow -ge $deadline) { throw "Lingering child did not outlive parent $($exitParent.Id); pid file '$lingerText'" }
        Start-Sleep -Seconds 1
    } while ($true)
    $lingerId = [int]$lingerText
    try { $lingerStart = (Get-Process -Id $lingerId -ErrorAction Stop).StartTime.ToUniversalTime() } catch { $lingerStart = $null }
    $Log = Join-Path $Run 'linger.log'
    @("log $Repo", "log $env:USERPROFILE") | Set-Content -LiteralPath $Log -Encoding utf8
    $script:DiagnosticsWritten = $false
    $script:HangRecorded = $false
    $script:SmokeGraceSeconds = $null
    $captured = & {
        try {
            $cleared = Wait-SmokeShutdown -Process $exitParent -OwnedPath $Run -GraceSeconds 2 -SmokeStart $lingerSmokeStart
            if ($cleared) { throw 'Lingering child was treated as a clear tree after the parent exited.' }
            Publish-SmokeHang -Process $exitParent -SmokeStart $lingerSmokeStart
            'HANG_RESULT pass'
        } catch {
            "HANG_RESULT fail: $($_.Exception.Message)"
        }
    } *>&1
    $text = Format-HangCapture $captured
    Write-Host $text
    try { $unrelated.Refresh() } catch { }
    $linger = Get-Process -Id $lingerId -ErrorAction SilentlyContinue
    if ($linger -and -not $linger.HasExited) { throw "Lingering child $lingerId was not killed" }
    if ($unrelated.HasExited) { throw "Unrelated process $($unrelated.Id) was killed after the parent exited" }
    if ($text -notlike '*HANG_RESULT fail:*' -or $text -like '*HANG_RESULT pass*') { throw "Linger cleanup was treated as a pass:`n$text" }
    if ($text -notlike '*remains running*') { throw "Linger cleanup did not report that the product remains running:`n$text" }
    if ($text -notlike '*after 2s shutdown grace*') { throw "Linger message did not report the 2s grace:`n$text" }
    if ($ShutdownGraceSeconds -ne 45) { throw "Installed smoke grace changed from 45 to $ShutdownGraceSeconds" }
    Write-Host "Linger cleanup test passed: parent $($exitParent.Id) exited, killed child $lingerId, left unrelated $($unrelated.Id) running."
} finally {
    foreach ($proc in @($parent, $exitParent, $unrelated)) {
        if ($proc) { try { $proc.Refresh(); if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } } catch { } }
    }
    foreach ($tracked in @(
        @{ Id = $childId; Start = $childStart },
        @{ Id = $lingerId; Start = $lingerStart }
    )) {
        if (-not $tracked.Id) { continue }
        $live = Get-Process -Id $tracked.Id -ErrorAction SilentlyContinue
        if (-not $live) { continue }
        $liveStart = $null
        try { $liveStart = $live.StartTime.ToUniversalTime() } catch { }
        if ($tracked.Start -and $liveStart -and ($tracked.Start.Ticks -ne $liveStart.Ticks)) {
            Write-Host "Skip pid=$($tracked.Id); PID reused (recorded start $($tracked.Start.ToString('o')), current start $($liveStart.ToString('o')))"
            continue
        }
        Stop-Process -Id $tracked.Id -Force -ErrorAction SilentlyContinue
    }
    foreach ($dir in @($Run, $scratch)) {
        if ($dir -and (Test-Path -LiteralPath $dir)) { Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue }
    }
}
