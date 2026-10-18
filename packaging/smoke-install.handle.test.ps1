# Read-only native adapter check. Never calls Terminate on any process.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'smoke-install.ps1')
$current = [System.Diagnostics.Process]::GetCurrentProcess()
$handle = $null
try {
    $handle = Open-SmokeProcessHandle -ProcessId $PID
    if ($handle.IsInvalid -or $handle.IsClosed) { throw 'Self-process handle is invalid' }
    if (-not (Test-SmokeRootIdentity $handle.CreationTimeUtc $current.StartTime)) {
        throw 'Native handle creation time does not identify this test process'
    }
} finally {
    if ($null -ne $handle) { $handle.Dispose() }
    $current.Dispose()
}
if (-not $handle.IsClosed) { throw 'Native self-process handle was not closed' }
Write-Host 'Native handle identity and disposal passed; no process was terminated.'
