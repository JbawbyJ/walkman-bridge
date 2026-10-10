#requires -Version 5.1
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Result = [ordered]@{ observed_at = [DateTime]::UtcNow.ToString('o'); passed = $false; checks = [ordered]@{}; errors = @() }
try {
    $Computer = Get-CimInstance Win32_ComputerSystem
    if ($Computer.Manufacturer -ne 'Microsoft Corporation' -or $Computer.Model -ne 'Virtual Machine') {
        throw 'Run this read-only baseline harness inside the dedicated Hyper-V guest.'
    }
    $Os = Get-CimInstance Win32_OperatingSystem
    $Version = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
    $Result.os = [ordered]@{ caption = $Os.Caption; build = $Os.BuildNumber; version = $Os.Version;
        edition = $Version.EditionID; architecture = $env:PROCESSOR_ARCHITECTURE; computer = $env:COMPUTERNAME;
        last_boot = $Os.LastBootUpTime.ToUniversalTime().ToString('o') }
    $Result.checks.windows_11_enterprise_eval = [int]$Os.BuildNumber -ge 22000 -and $Version.EditionID -eq 'EnterpriseEval' -and $env:PROCESSOR_ARCHITECTURE -eq 'AMD64'
    $Result.checks.administrator = $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    try {
        $Defender = Get-MpComputerStatus
        $Result.defender = [ordered]@{ antivirus_enabled = $Defender.AntivirusEnabled; service_enabled = $Defender.AMServiceEnabled;
            real_time_protection_enabled = $Defender.RealTimeProtectionEnabled; behavior_monitor_enabled = $Defender.BehaviorMonitorEnabled;
            signature_version = $Defender.AntivirusSignatureVersion; signature_updated = $Defender.AntivirusSignatureLastUpdated.ToUniversalTime().ToString('o');
            running_mode = $Defender.AMRunningMode; signatures_out_of_date = $Defender.DefenderSignaturesOutOfDate }
        $Result.checks.defender_active = $Defender.AntivirusEnabled -and $Defender.AMServiceEnabled -and $Defender.RealTimeProtectionEnabled -and $Defender.BehaviorMonitorEnabled -and $Defender.AMRunningMode -eq 'Normal'
        $Result.checks.defender_signatures_current = -not $Defender.DefenderSignaturesOutOfDate -and $Defender.AntivirusSignatureLastUpdated -gt (Get-Date).AddDays(-7)
    } catch { $Result.checks.defender_active = $false; $Result.errors += ('Defender: ' + $_.Exception.Message) }
    try { $Result.checks.secure_boot_enabled = [bool](Confirm-SecureBootUEFI) }
    catch { $Result.checks.secure_boot_enabled = $false; $Result.errors += ('Secure Boot: ' + $_.Exception.Message) }
    try { $Tpm = Get-Tpm; $Result.checks.tpm_ready = $Tpm.TpmPresent -and $Tpm.TpmReady }
    catch { $Result.checks.tpm_ready = $false; $Result.errors += ('TPM: ' + $_.Exception.Message) }
    $Tools = foreach ($Tool in @('node','npm','python','python3','py','java','javac','ffmpeg','dotnet','git','msbuild','devenv')) {
        $Paths = @(Get-Command $Tool -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object Source)
        [pscustomobject]@{ name = $Tool; paths = $Paths; installed_runtime_found = @($Paths | Where-Object { $_ -notlike '*\Microsoft\WindowsApps\*' }).Count -gt 0 }
    }
    $Installed = @()
    foreach ($RegistryPath in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall')) {
        if (Test-Path -LiteralPath $RegistryPath -ErrorAction Stop) {
            $Installed += @(Get-ChildItem -LiteralPath $RegistryPath -ErrorAction Stop | Get-ItemProperty -ErrorAction Stop | Where-Object DisplayName | Select-Object DisplayName,DisplayVersion)
        }
    }
    $DevProducts = @($Installed | Where-Object { $_.DisplayName -match '(?i)(Node\.js|Python|Java Development|OpenJDK|Temurin|\.NET SDK|Visual Studio|Git for Windows|FFmpeg)' })
    $AppProducts = @($Installed | Where-Object { $_.DisplayName -match '(?i)(Walkman Bridge|Red Lotus Player)' })
    $KnownDevPaths = @()
    foreach ($Pattern in @('C:\Program Files\nodejs','C:\Program Files\Git','C:\Program Files\Java','C:\Program Files\dotnet\sdk','C:\Program Files\Microsoft Visual Studio','C:\Python*',"$env:LOCALAPPDATA\Programs\Python")) {
        if (Test-Path -Path $Pattern -ErrorAction Stop) {
            $KnownDevPaths += @(Get-Item -Path $Pattern -ErrorAction Stop | ForEach-Object FullName)
        }
    }
    $Result.tools = @($Tools)
    $Result.developer_products = $DevProducts
    $Result.developer_paths = $KnownDevPaths
    $Result.product_installations = $AppProducts
    $Result.checks.no_developer_tools = @($Tools | Where-Object installed_runtime_found).Count -eq 0 -and $DevProducts.Count -eq 0 -and $KnownDevPaths.Count -eq 0
    $Result.checks.products_not_installed = $AppProducts.Count -eq 0 -and -not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Walkman Bridge')) -and -not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Red Lotus Player'))
    $Result.checks.no_pending_reboot = -not (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -and -not (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
    $Result.passed = $Result.errors.Count -eq 0 -and @($Result.checks.Values | Where-Object { $_ -ne $true }).Count -eq 0
} catch { $Result.errors += $_.Exception.Message }
$Result | ConvertTo-Json -Depth 10
