[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('bridge', 'player')][string]$Product,
    [Parameter(Mandatory)][string]$Installer
)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$Name = if ($Product -eq 'bridge') { 'Walkman Bridge' } else { 'Red Lotus Player' }
$Executable = if ($Product -eq 'bridge') { 'Walkman Bridge.exe' } else { 'Red Lotus Player.exe' }
$DataName = if ($Product -eq 'bridge') { 'Walkman Bridge' } else { 'Red Lotus Player' }
$LegacyName = 'Walkman Bridge ' + [char]0x2014 + ' Night Ops'
function Registrations {
    $RegistryRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
    if (-not (Test-Path -LiteralPath $RegistryRoot -ErrorAction Stop)) { return @() }
    @(Get-ChildItem -LiteralPath $RegistryRoot -ErrorAction Stop | Get-ItemProperty -ErrorAction Stop | Where-Object { $_.DisplayName -eq $Name -or ($Product -eq 'bridge' -and $_.DisplayName -eq $LegacyName) })
}
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
    if (-not $Process.WaitForExit(120000)) { throw "Packaged smoke did not finish; process $($Process.Id) remains for diagnosis" }
    $Process.WaitForExit(); $Process.Refresh()
    $Log = Join-Path $LocalData "$DataName\redlotus.log"
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
        if ($Process -and -not $Process.HasExited) { throw 'Product remains running; cleanup deferred for diagnosis.' }
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
