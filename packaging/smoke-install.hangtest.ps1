# Exercises the installed-smoke hang path without an installer.
# A hung parent and its child must be killed, diagnostics must redact paths,
# the cleanup result must fail, and an older unrelated sleeper must survive.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'smoke-install.ps1')

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
$childId = 0
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
    $Log = Join-Path $Run 'redlotus.log'
    @("log $Repo", "log $env:USERPROFILE") | Set-Content -LiteralPath $Log -Encoding utf8
    $Executable = 'hang-parent'
    $script:DiagnosticsWritten = $false
    $script:HangRecorded = $false
    $captured = & {
        try {
            $cleared = Wait-SmokeShutdown -Process $parent -OwnedPath $Run -GraceSeconds 2
            if ($cleared) { throw 'Hang cleared during grace; the fake product was expected to stay running.' }
            Publish-SmokeHang -Process $parent
            'HANG_RESULT pass'
        } catch {
            "HANG_RESULT fail: $($_.Exception.Message)"
        }
    } *>&1
    $text = (($captured | ForEach-Object {
        if ($_ -is [System.Management.Automation.InformationRecord]) { [string]$_.MessageData }
        elseif ($_ -is [System.Management.Automation.ErrorRecord]) { [string]$_.Exception.Message }
        else { [string]$_ }
    }) -join "`n")
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
    if ($text -notlike '*remains running*') { throw "Hang cleanup did not fail closed:`n$text" }
    Write-Host "Hang cleanup test passed: killed parent $($parent.Id) and child $childId; left unrelated $($unrelated.Id) running."
} finally {
    foreach ($proc in @($parent, $unrelated)) {
        if ($proc) { try { $proc.Refresh(); if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } } catch { } }
    }
    if ($childId) { Stop-Process -Id $childId -Force -ErrorAction SilentlyContinue }
    foreach ($dir in @($Run, $scratch)) {
        if ($dir -and (Test-Path -LiteralPath $dir)) { Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue }
    }
}
