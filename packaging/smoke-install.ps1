[CmdletBinding()]
param(
    [Parameter()][ValidateSet('bridge', 'player')][string]$Product,
    [Parameter()][string]$Installer
)
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Name = if ($Product -eq 'bridge') { 'Walkman Bridge' } else { 'Red Lotus Player' }
$Executable = if ($Product -eq 'bridge') { 'Walkman Bridge.exe' } else { 'Red Lotus Player.exe' }
$DataName = if ($Product -eq 'bridge') { 'Walkman Bridge' } else { 'Red Lotus Player' }
$LegacyName = 'Walkman Bridge ' + [char]0x2014 + ' Night Ops'
# --smoke requests shutdown. Poll the owned process tree before calling a live product a hang.
$ShutdownGraceSeconds = 45
$DiagnosticsWritten = $false
$HangRecorded = $false
$GraceConsumed = $false
function Registrations {
    $RegistryRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
    if (-not (Test-Path -LiteralPath $RegistryRoot -ErrorAction Stop)) { return @() }
    @(Get-ChildItem -LiteralPath $RegistryRoot -ErrorAction Stop | Get-ItemProperty -ErrorAction Stop | Where-Object { $_.DisplayName -eq $Name -or ($Product -eq 'bridge' -and $_.DisplayName -eq $LegacyName) })
}
function ConvertTo-SmokeInstant($Value) {
    if ($null -eq $Value -or $Value -eq '') { return $null }
    $instant = $Value
    if ($instant -isnot [datetime]) {
        try { $instant = [datetime]$instant } catch { return $null }
    }
    if ($instant.Kind -eq [DateTimeKind]::Utc) { return $instant }
    return $instant.ToUniversalTime()
}
function Copy-OwnedProcess($Item) {
    [pscustomobject]@{
        Name = [string]$Item.Name
        ProcessId = [int]$Item.ProcessId
        ParentProcessId = [int]$Item.ParentProcessId
        CommandLine = [string]$Item.CommandLine
        ExecutablePath = [string]$Item.ExecutablePath
        CreationDate = ConvertTo-SmokeInstant $Item.CreationDate
    }
}
function Hide-SmokePath([string]$Text) {
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $pairs = @(
        @{ Path = $Repo; Token = '<REPO>' },
        @{ Path = $env:USERPROFILE; Token = '<USERPROFILE>' }
    )
    foreach ($pair in $pairs) {
        $path = [string]$pair.Path
        if (-not $path) { continue }
        $path = $path.TrimEnd('\', '/')
        foreach ($form in @($path, ($path.Replace('\', '/')))) {
            if (-not $form) { continue }
            $boundary = [regex]::Escape($form) + '(?=$|[\\/]|[\s"''])'
            $Text = [regex]::Replace($Text, $boundary, $pair.Token, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        }
    }
    return $Text
}
function Get-SmokeProcesses {
    param([int]$RootProcessId, [string]$OwnedPath)
    try { $snapshot = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop) }
    catch { throw "Win32_Process query failed: $($_.Exception.Message)" }
    $byId = @{}
    $childrenOf = @{}
    foreach ($item in $snapshot) {
        $idKey = [string][int]$item.ProcessId
        $byId[$idKey] = $item
        $parentKey = [string][int]$item.ParentProcessId
        if (-not $childrenOf.ContainsKey($parentKey)) { $childrenOf[$parentKey] = [System.Collections.Generic.List[object]]::new() }
        [void]$childrenOf[$parentKey].Add($item)
    }
    $found = [System.Collections.Generic.List[object]]::new()
    $seen = @{}
    $pending = [System.Collections.Generic.Queue[string]]::new()
    if ($RootProcessId -gt 0) { $pending.Enqueue([string]$RootProcessId) }
    while ($pending.Count -gt 0) {
        $current = $pending.Dequeue()
        if ($seen.ContainsKey($current)) { continue }
        $seen[$current] = $true
        $keep = $false
        if ($byId.ContainsKey($current)) {
            $copied = Copy-OwnedProcess $byId[$current]
            $keep = $true
            if ([int]$copied.ProcessId -ne $RootProcessId) {
                $parentKey = [string][int]$copied.ParentProcessId
                $parentStart = $null
                if ($byId.ContainsKey($parentKey)) { $parentStart = ConvertTo-SmokeInstant $byId[$parentKey].CreationDate }
                # A reused PID is older than its supposed parent. Real children start with or after the parent.
                if (-not $parentStart -or -not $copied.CreationDate -or ($copied.CreationDate -lt $parentStart)) { $keep = $false }
            }
            if ($keep) { [void]$found.Add($copied) }
        }
        if ($keep -and $childrenOf.ContainsKey($current)) {
            foreach ($child in $childrenOf[$current]) { $pending.Enqueue([string][int]$child.ProcessId) }
        }
    }
    $smokeStart = $null
    if ($RootProcessId -gt 0 -and $byId.ContainsKey([string]$RootProcessId)) {
        $smokeStart = ConvertTo-SmokeInstant $byId[[string]$RootProcessId].CreationDate
    }
    $owned = if ($OwnedPath) { $OwnedPath.TrimEnd('\') } else { '' }
    if ($owned -and $smokeStart) {
        foreach ($item in $snapshot) {
            $idKey = [string][int]$item.ProcessId
            if ($seen.ContainsKey($idKey)) { continue }
            $command = [string]$item.CommandLine
            $executablePath = [string]$item.ExecutablePath
            $ownedHit = ($command -and $command.IndexOf($owned, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) -or ($executablePath -and $executablePath.IndexOf($owned, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
            $itemStart = ConvertTo-SmokeInstant $item.CreationDate
            if ($ownedHit -and $itemStart -and ($itemStart -ge $smokeStart)) {
                [void]$found.Add((Copy-OwnedProcess $item)); $seen[$idKey] = $true
            }
        }
    }
    return @($found | Where-Object { [int]$_.ProcessId -gt 0 -and [int]$_.ProcessId -ne $PID })
}
function Test-SmokeTreeClear {
    param($Process, [string]$OwnedPath)
    if ($Process) { try { $Process.Refresh() } catch { } }
    $rootId = if ($Process) { [int]$Process.Id } else { 0 }
    if ($Process -and -not $Process.HasExited) { return $false }
    $extras = @(Get-SmokeProcesses -RootProcessId $rootId -OwnedPath $OwnedPath | Where-Object { [int]$_.ProcessId -ne $rootId })
    return ($extras.Count -eq 0)
}
function Wait-SmokeShutdown {
    param($Process, [string]$OwnedPath, [int]$GraceSeconds)
    $deadline = [datetime]::UtcNow.AddSeconds($GraceSeconds)
    while ($true) {
        if (Test-SmokeTreeClear -Process $Process -OwnedPath $OwnedPath) { return $true }
        if ([datetime]::UtcNow -ge $deadline) { return $false }
        Start-Sleep -Seconds 1
    }
}
function Write-SmokeDiagnostics {
    param($Processes, [string]$LogPath, $Process)
    Write-Host 'Product processes still running after shutdown grace:'
    $rows = @($Processes)
    $knownRoot = $Process -and @($rows | Where-Object { [int]$_.ProcessId -eq [int]$Process.Id }).Count -gt 0
    if ($Process -and -not $Process.HasExited -and -not $knownRoot) {
        $started = ''
        try { $started = $Process.StartTime.ToUniversalTime().ToString('o') } catch { }
        Write-Host "name=$Executable pid=$($Process.Id) parent=tracked started=$started command="
    }
    foreach ($item in $rows) {
        $started = $item.CreationDate
        if ($started -is [datetime]) { $started = $started.ToUniversalTime().ToString('o') }
        $command = [string]$item.CommandLine
        if (-not $command) { $command = [string]$item.ExecutablePath }
        $command = Hide-SmokePath $command
        if ($command.Length -gt 2000) { $command = $command.Substring(0, 2000) + '...' }
        Write-Host ("name={0} pid={1} parent={2} started={3} command={4}" -f $item.Name, $item.ProcessId, $item.ParentProcessId, $started, $command)
    }
    if ($LogPath -and (Test-Path -LiteralPath $LogPath)) {
        Write-Host '--- app log tail ---'
        Get-Content -LiteralPath $LogPath -Tail 40 | ForEach-Object { Write-Host (Hide-SmokePath $_) }
    } else {
        Write-Host ("App log missing: {0}" -f (Hide-SmokePath $LogPath))
    }
}
function Get-SmokeStopSkip {
    param($Item, $SmokeStart)
    $processId = [int]$Item.ProcessId
    try { $live = @(Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId={0}" -f $processId) -ErrorAction Stop) }
    catch { throw "Win32_Process query failed: $($_.Exception.Message)" }
    $liveStart = if ($live) { ConvertTo-SmokeInstant $live[0].CreationDate } else { $null }
    $recorded = ConvertTo-SmokeInstant $Item.CreationDate
    $name = [string]$Item.Name
    if (-not $live) { return "Skip pid=$processId name=$name; process is no longer running" }
    $currentText = if ($liveStart) { $liveStart.ToString('o') } else { 'unknown' }
    if ($recorded -and $liveStart -and ($recorded.Ticks -ne $liveStart.Ticks)) {
        return "Skip pid=$processId name=$name; PID reused (recorded start $($recorded.ToString('o')), current start $currentText)"
    }
    if ($SmokeStart -and $liveStart -and ($liveStart -lt $SmokeStart)) {
        return "Skip pid=$processId name=$name; started before the smoke process (start $currentText, smoke $($SmokeStart.ToString('o')))"
    }
    return $null
}
function Stop-SmokeLeftovers {
    param($Processes, $Process)
    $pending = [System.Collections.Generic.List[object]]::new()
    foreach ($item in @($Processes)) {
        if ([int]$item.ProcessId -gt 0 -and [int]$item.ProcessId -ne $PID) { [void]$pending.Add($item) }
    }
    $smokeProcessId = if ($Process) { [int]$Process.Id } else { 0 }
    $smokeStart = $null
    foreach ($item in @($pending)) {
        if ($smokeProcessId -and [int]$item.ProcessId -eq $smokeProcessId -and $item.CreationDate) { $smokeStart = ConvertTo-SmokeInstant $item.CreationDate; break }
    }
    if ($Process -and -not $Process.HasExited -and $smokeProcessId -ne $PID) {
        if (-not @($pending | Where-Object { [int]$_.ProcessId -eq $smokeProcessId })) {
            try { $liveRoot = @(Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId={0}" -f $smokeProcessId) -ErrorAction Stop) }
            catch { throw "Win32_Process query failed: $($_.Exception.Message)" }
            if ($liveRoot) {
                $copied = Copy-OwnedProcess $liveRoot[0]
                [void]$pending.Add($copied)
                if (-not $smokeStart) { $smokeStart = $copied.CreationDate }
            }
        }
    }
    if (-not $smokeStart -and $Process) { try { $smokeStart = $Process.StartTime.ToUniversalTime() } catch { } }
    while ($pending.Count -gt 0) {
        $leaves = [System.Collections.Generic.List[object]]::new()
        foreach ($item in @($pending)) {
            $isParent = $false
            foreach ($other in @($pending)) {
                if ([int]$other.ProcessId -ne [int]$item.ProcessId -and [int]$other.ParentProcessId -eq [int]$item.ProcessId) { $isParent = $true; break }
            }
            if (-not $isParent) { [void]$leaves.Add($item) }
        }
        if ($leaves.Count -eq 0) { foreach ($item in @($pending)) { [void]$leaves.Add($item) } }
        foreach ($item in @($leaves)) {
            $skip = Get-SmokeStopSkip -Item $item -SmokeStart $smokeStart
            if ($skip) { Write-Host $skip }
            else {
                try { Stop-Process -Id ([int]$item.ProcessId) -Force -ErrorAction Stop }
                catch { Write-Host "Could not terminate pid=$($item.ProcessId): $($_.Exception.Message)" }
            }
            for ($index = $pending.Count - 1; $index -ge 0; $index--) {
                if ([int]$pending[$index].ProcessId -eq [int]$item.ProcessId) { $pending.RemoveAt($index) }
            }
        }
    }
    if ($Process) { try { $Process.Refresh() } catch { } }
}
function Publish-SmokeHang {
    param($Process)
    $rootId = if ($Process) { [int]$Process.Id } else { 0 }
    $remaining = @(Get-SmokeProcesses -RootProcessId $rootId -OwnedPath $Run)
    if (-not $script:DiagnosticsWritten) {
        Write-SmokeDiagnostics -Processes $remaining -LogPath $Log -Process $Process
        $script:DiagnosticsWritten = $true
    }
    Stop-SmokeLeftovers -Processes $remaining -Process $Process
    $settle = [datetime]::UtcNow.AddSeconds(5)
    do {
        if (Test-SmokeTreeClear -Process $Process -OwnedPath $Run) { break }
        if ([datetime]::UtcNow -ge $settle) { break }
        Start-Sleep -Seconds 1
    } while ($true)
    $script:HangRecorded = $true
    $tracked = if ($Process) { $Process.Id } else { 'unknown' }
    throw "Product remains running after ${ShutdownGraceSeconds}s shutdown grace; process $tracked diagnostics logged and leftovers terminated."
}
if ($MyInvocation.InvocationName -eq '.') { return }
$ErrorActionPreference = 'Stop'
if (-not $Product -or -not $Installer) { throw 'Product and Installer are required.' }
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$Existing = @(Registrations)
if ($Existing) { throw "An existing $Name installation is registered; installer smoke will not replace it." }
if (Get-Process -Name $Name,'Walkman Bridge Night Ops' -ErrorAction SilentlyContinue) { throw 'A user product is running.' }
foreach ($Directory in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))) {
    foreach ($ShortcutName in @($Name,$LegacyName)) {
        if (Test-Path -LiteralPath (Join-Path $Directory ($ShortcutName + '.lnk'))) { throw 'An existing user shortcut is present; smoke testing refuses to replace it.' }
    }
}
$Run = Join-Path $Repo ("packaging\build\install-smoke\" + $Product + '-' + [guid]::NewGuid().ToString('N'))
$InstallDirectory = Join-Path $Run 'installed'
$LocalData = Join-Path $Run 'local-data'
$RoamingData = Join-Path $Run 'roaming-data'
New-Item -ItemType Directory -Force -Path $Run,$LocalData,$RoamingData | Out-Null
$OldLocal = $env:LOCALAPPDATA
$OldRoaming = $env:APPDATA
$OldNode = $env:ELECTRON_RUN_AS_NODE
$Installed = $false
$BodyPassed = $false
$Process = $null
$Log = Join-Path $LocalData "$DataName\redlotus.log"
$Proof = [ordered]@{product=$Product;installer=$Installer;installer_sha256=(Get-FileHash -LiteralPath $Installer).Hash;installed_path=$InstallDirectory;passed=$false;cleanup=@{passed=$false}}
try {
    $env:LOCALAPPDATA = $LocalData
    $env:APPDATA = $RoamingData
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    $Install = Start-Process -FilePath $Installer -ArgumentList @('/S', "/D=$InstallDirectory") -WindowStyle Hidden -Wait -PassThru
    if ($Install.ExitCode -ne 0) { throw "Installer exited $($Install.ExitCode)" }
    $Installed = $true
    $AppPath = Join-Path $InstallDirectory $Executable
    if (-not (Test-Path -LiteralPath $AppPath)) { throw 'Installer did not create the expected executable' }
    $Process = Start-Process -FilePath $AppPath -ArgumentList '--smoke' -WindowStyle Hidden -PassThru
    $null = $Process.Handle
    $smokeExited = $Process.WaitForExit(120000)
    if ($smokeExited) { $Process.WaitForExit(); $Process.Refresh() }
    $script:GraceConsumed = $true
    if (-not (Wait-SmokeShutdown -Process $Process -OwnedPath $Run -GraceSeconds $ShutdownGraceSeconds)) {
        Publish-SmokeHang -Process $Process
    }
    $Process.WaitForExit(); $Process.Refresh()
    if ($Process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $Log) -or -not (Select-String -LiteralPath $Log -Pattern 'SMOKE OK' -Quiet)) {
        throw "Packaged smoke did not prove authenticated startup and drain; inspect $Log"
    }
    $Proof.log = $Log; $Proof.exit_code = $Process.ExitCode
    $BodyPassed = $true
} catch {
    $Proof.failure = $_.Exception.Message
    throw
} finally {
    try {
      if ($Installed) {
        if (-not $script:GraceConsumed -and -not (Test-SmokeTreeClear -Process $Process -OwnedPath $Run)) {
            $script:GraceConsumed = $true
            if (-not (Wait-SmokeShutdown -Process $Process -OwnedPath $Run -GraceSeconds $ShutdownGraceSeconds)) {
                Publish-SmokeHang -Process $Process
            }
        }
        if (-not (Test-SmokeTreeClear -Process $Process -OwnedPath $Run)) {
            if (-not $script:HangRecorded) { Publish-SmokeHang -Process $Process }
            throw "Product remains running after ${ShutdownGraceSeconds}s shutdown grace; diagnostics logged and leftovers terminated."
        }
        $Uninstaller = @(Get-ChildItem -LiteralPath $InstallDirectory -Filter 'Uninstall*.exe' -File)
        if ($Uninstaller.Count -ne 1) { throw 'Exactly one test-owned uninstaller is required.' }
        if ($Uninstaller.Count -eq 1) {
            $Removal = Start-Process -FilePath $Uninstaller[0].FullName -ArgumentList @('/S', "_?=$InstallDirectory") -WindowStyle Hidden -Wait -PassThru
            if ($Removal.ExitCode -ne 0) { throw "Smoke uninstaller exited $($Removal.ExitCode); inspect $InstallDirectory" }
        }
        $Remaining = @(Registrations)
        if ($Remaining.Count) { throw 'Uninstall left a product registration.' }
        foreach ($Directory in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'),(Join-Path $RoamingData 'Microsoft/Windows/Start Menu/Programs'))) {
            foreach ($ShortcutName in @($Name,$LegacyName)) {
                if (Test-Path -LiteralPath (Join-Path $Directory ($ShortcutName + '.lnk'))) { throw 'Uninstall left a product shortcut.' }
            }
        }
        $Proof.cleanup = @{passed=$true;registration_removed=$true;shortcuts_removed=$true}
      }
    } catch {
        $Proof.cleanup = @{passed=$false;failure=$_.Exception.Message}
        throw
    } finally {
        $env:LOCALAPPDATA = $OldLocal
        $env:APPDATA = $OldRoaming
        $env:ELECTRON_RUN_AS_NODE = $OldNode
        $Proof.passed = $BodyPassed -and $Proof.cleanup.passed
        $Proof | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Run 'result.json') -Encoding utf8
    }
}
Write-Host "Installed smoke passed: $Product ($Run)"
