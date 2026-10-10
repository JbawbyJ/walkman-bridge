[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$InputRoot = 'C:\RedLotusInput'
$ResultRoot = 'C:\RedLotusResults'
$Run = [ordered]@{ schema = 2; started_at = [DateTime]::UtcNow.ToString('o'); phase = 'inventory'; passed = $false; products = @(); phases = @() }
$DiagnosticRoot = Join-Path $ResultRoot 'diagnostics'
$Diagnostics = [ordered]@{ policy_changes = $false; commands = @(); events = @() }
function Save-Result {
    $Run | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $ResultRoot 'guest-result.json') -Encoding UTF8
}
function Save-Diagnostics {
    $Diagnostics | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $DiagnosticRoot 'index.json') -Encoding UTF8
}
function Get-FailureDetails($Record) {
    $Chain = @()
    $Failure = $Record.Exception
    while ($Failure) {
        $Chain += [pscustomobject]@{ type = $Failure.GetType().FullName; message = $Failure.Message;
            hresult = $Failure.HResult; native_error_code = $Failure.NativeErrorCode }
        $Failure = $Failure.InnerException
    }
    return [pscustomobject]@{ id = $Record.FullyQualifiedErrorId; category = [string]$Record.CategoryInfo; exceptions = $Chain }
}
function Get-FailureClass($Details, [string]$Message, [string]$Phase) {
    $Codes = @($Details.exceptions | ForEach-Object { $_.native_error_code })
    $Messages = $Message + ' ' + (($Details.exceptions | ForEach-Object { $_.message }) -join ' ')
    if (@($Codes | Where-Object { $_ -in @(577, 1260, 4551) }).Count -or $Messages -match '(?i)application control|blocked by.*policy|digital signature.*verified|AppLocker') { return 'application_control_block' }
    if ($Codes -contains 740 -or $Messages -match '(?i)requires elevation|elevation required') { return 'elevation_required' }
    if ($Codes -contains 5 -or $Messages -match '(?i)access.*denied|permission.*denied|unauthorized') { return 'permission_denied' }
    if ($Messages -match 'exceeded \d+ ms') { return 'timeout' }
    if ($Phase -like 'defender*') { return 'defender_probe_failed' }
    return 'application_or_harness_failure'
}
function Set-TestPhase($Target, [string]$Phase) {
    $Target.phase = $Phase
    $Entry = [ordered]@{ phase = $Phase; at = [DateTime]::UtcNow.ToString('o') }
    if ($Target.Contains('product')) { $Entry.product = $Target.product }
    $Run.phases += $Entry
    Save-Result
}
function Start-TestProcess([string]$FilePath, [string[]]$ArgumentList, [switch]$PassThru,
    [string]$WindowStyle, [string]$RedirectStandardOutput, [string]$RedirectStandardError) {
    # Keep the Process handle we opened. Start-Process/Refresh previously produced
    # null exit codes for fast children in Sandbox, which cannot prove success.
    $Info = New-Object System.Diagnostics.ProcessStartInfo
    $Info.FileName = $FilePath
    $Info.Arguments = $ArgumentList -join ' '
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    $Info.RedirectStandardOutput = $true
    $Info.RedirectStandardError = $true
    $Child = New-Object System.Diagnostics.Process
    $Child.StartInfo = $Info
    if (-not $Child.Start()) { throw "Could not start $FilePath" }
    $Child | Add-Member -NotePropertyName OutputTask -NotePropertyValue $Child.StandardOutput.ReadToEndAsync()
    $Child | Add-Member -NotePropertyName ErrorTask -NotePropertyValue $Child.StandardError.ReadToEndAsync()
    $Child | Add-Member -NotePropertyName OutputPath -NotePropertyValue $RedirectStandardOutput
    $Child | Add-Member -NotePropertyName ErrorPath -NotePropertyValue $RedirectStandardError
    return $Child
}
function Get-SignatureRecord([string]$Path) {
    try {
        $Signature = Get-AuthenticodeSignature -LiteralPath $Path -ErrorAction Stop
        return @{ path = $Path; status = [string]$Signature.Status; message = $Signature.StatusMessage;
            signer = $Signature.SignerCertificate.Subject; thumbprint = $Signature.SignerCertificate.Thumbprint }
    } catch { return @{ path = $Path; error = Get-FailureDetails $_ } }
}
function Invoke-DiagnosticCommand([string]$Name, [string]$Executable, [string[]]$Arguments) {
    # Fixed read-only commands only. Failures are diagnostic evidence, not negative status.
    $Entry = [ordered]@{ name = $Name; executable = $Executable; arguments = $Arguments;
        started_at = [DateTime]::UtcNow.ToString('o'); completed = $false }
    $Process = $null
    try {
        $Process = Start-TestProcess -FilePath $Executable -ArgumentList $Arguments -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $DiagnosticRoot ($Name + '-stdout.log')) `
            -RedirectStandardError (Join-Path $DiagnosticRoot ($Name + '-stderr.log'))
        $Entry.exit_code = Wait-TestProcess $Process 15000 $Name
        $Entry.completed = $true
    } catch { $Entry.error = Get-FailureDetails $_; $Entry.failure_class = Get-FailureClass $Entry.error $_.Exception.Message $Name }
    finally {
        if ($Process -and -not $Process.HasExited) { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue; $Entry.timed_out = $true }
        $Entry.finished_at = [DateTime]::UtcNow.ToString('o')
        $Diagnostics.commands += $Entry
        Save-Diagnostics
    }
}
function Save-PolicyEvents([string]$Stage) {
    foreach ($Channel in @('Microsoft-Windows-CodeIntegrity/Operational', 'Microsoft-Windows-AppLocker/MSI and Script',
        'Microsoft-Windows-AppLocker/EXE and DLL', 'Microsoft-Windows-Windows Defender/Operational')) {
        $Name = $Stage + '-' + ($Channel -replace '[^A-Za-z0-9-]', '_')
        $Entry = [ordered]@{ stage = $Stage; channel = $Channel; queried_at = [DateTime]::UtcNow.ToString('o');
            since = $Run.started_at; limit = 200; available = $false }
        try {
            # Store original XML to retain ActivityID, policy IDs, hashes and signing details.
            $Events = @(Get-WinEvent -FilterHashtable @{ LogName = $Channel; StartTime = [DateTime]::Parse($Run.started_at) } -MaxEvents 200 -ErrorAction Stop)
            $Xml = '<Events>' + (($Events | ForEach-Object { $_.ToXml() }) -join "`r`n") + '</Events>'
            $Xml | Set-Content -LiteralPath (Join-Path $DiagnosticRoot ($Name + '.xml')) -Encoding UTF8
            $Entry.available = $true
            $Entry.count = $Events.Count
            $Entry.records = @($Events | ForEach-Object { @{ id = $_.Id; record_id = $_.RecordId; at = $_.TimeCreated.ToUniversalTime().ToString('o'); message = $_.Message } })
            $Entry.possibly_truncated = $Events.Count -eq 200
        } catch {
            if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') {
                $Entry.available = $true; $Entry.count = 0; $Entry.possibly_truncated = $false
                '<Events />' | Set-Content -LiteralPath (Join-Path $DiagnosticRoot ($Name + '.xml')) -Encoding UTF8
            } else { $Entry.error = Get-FailureDetails $_ }
        }
        $Diagnostics.events += $Entry
        # Native query is an independent fallback when the PowerShell event API is denied.
        Invoke-DiagnosticCommand $Name "$env:SystemRoot\System32\wevtutil.exe" @('qe', ('"' + $Channel + '"'), '/c:200', '/rd:true', '/f:xml', '/e:Events', '"/q:*[System[TimeCreated[timediff(@SystemTime) <= 1800000]]]"')
    }
    Save-Diagnostics
}
function Wait-TestProcess($TestProcess, [int]$Milliseconds, [string]$Stage) {
    try {
        if (-not $TestProcess.WaitForExit($Milliseconds)) { $TestProcess.Kill(); throw "$Stage exceeded $Milliseconds ms (guest PID $($TestProcess.Id))" }
        $Code = $TestProcess.ExitCode
        if ($null -eq $Code) { throw "$Stage returned no exit code" }
        return $Code
    } finally {
        if ($TestProcess.HasExited) {
            if ($TestProcess.OutputPath -and $TestProcess.OutputTask.Wait(5000)) { $TestProcess.OutputTask.Result | Set-Content -LiteralPath $TestProcess.OutputPath -Encoding UTF8 }
            if ($TestProcess.ErrorPath -and $TestProcess.ErrorTask.Wait(5000)) { $TestProcess.ErrorTask.Result | Set-Content -LiteralPath $TestProcess.ErrorPath -Encoding UTF8 }
        }
    }
}
function Invoke-RuntimeProbe([string]$Name, [string]$Executable, [string[]]$Arguments, [int]$ExpectedExit) {
    $Stdout = Join-Path $ResultRoot ($Product.product + '-' + $Name + '-stdout.log')
    $Stderr = Join-Path $ResultRoot ($Product.product + '-' + $Name + '-stderr.log')
    Set-TestPhase $Row ('runtime_' + $Name + '_launch')
    $Signature = Get-SignatureRecord $Executable
    $Row.runtime_signatures += $Signature
    $Probe = Start-TestProcess -FilePath $Executable -ArgumentList $Arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr
    Set-TestPhase $Row ('runtime_' + $Name + '_wait')
    try { $Code = Wait-TestProcess $Probe 30000 "Bundled $Name probe" }
    finally { if (-not $Probe.HasExited) { Stop-Process -Id $Probe.Id -Force -ErrorAction SilentlyContinue } }
    if ($Code -ne $ExpectedExit) { throw "Bundled $Name returned $Code, expected $ExpectedExit" }
    if ($Name -eq 'helper' -and -not (Select-String -LiteralPath $Stderr -Pattern 'scanner_exchange_failed' -Quiet)) { throw 'Native helper did not reject invalid arguments as expected' }
    return [pscustomobject]@{ name = $Name; exit_code = $Code; passed = $true }
}
try {
    New-Item -ItemType Directory -Path $DiagnosticRoot -Force | Out-Null
    $Run.diagnostics = 'diagnostics/index.json'
    Save-Result
    $Diagnostics.powershell_language_mode = [string]$ExecutionContext.SessionState.LanguageMode
    $Diagnostics.execution_policy = @(Get-ExecutionPolicy -List | ForEach-Object { @{ scope = [string]$_.Scope; policy = [string]$_.ExecutionPolicy } })
    try {
        $Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $Principal = New-Object System.Security.Principal.WindowsPrincipal($Identity)
        $Diagnostics.token = @{ name = $Identity.Name; administrator = $Principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator) }
        # Read the actual token; do not request elevation or change privileges.
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class RedLotusToken {
  [DllImport("advapi32.dll", SetLastError=true)]
  static extern bool GetTokenInformation(IntPtr token, int kind, out int value, int size, out int length);
  public static int Read(IntPtr token, int kind) {
    int value, length;
    if (!GetTokenInformation(token,kind,out value,4,out length)) throw new System.ComponentModel.Win32Exception();
    return value;
  }
}
'@
        $Diagnostics.token.elevated = [RedLotusToken]::Read($Identity.Token, 20) -ne 0
        $Diagnostics.token.elevation_type = [RedLotusToken]::Read($Identity.Token, 18)
        $Diagnostics.token.elevation_type_key = @{ '1' = 'default'; '2' = 'full'; '3' = 'limited' }
    } catch { $Diagnostics.token_error = Get-FailureDetails $_ }
    Invoke-DiagnosticCommand 'token' "$env:SystemRoot\System32\whoami.exe" @('/all')
    Invoke-DiagnosticCommand 'app-control-policies' "$env:SystemRoot\System32\CiTool.exe" @('-lp', '-json')
    foreach ($ServiceName in @('WinDefend', 'WdNisSvc', 'wscsvc', 'SecurityHealthService', 'Winmgmt', 'AppIDSvc')) {
        Invoke-DiagnosticCommand ('service-' + $ServiceName) "$env:SystemRoot\System32\sc.exe" @('query', $ServiceName)
    }
    try {
        $Service = Get-Service -Name WinDefend -ErrorAction Stop
        $Diagnostics.defender_service = @{ name = $Service.Name; status = [string]$Service.Status; start_type = [string]$Service.StartType }
    }
    catch { $Diagnostics.defender_service_error = Get-FailureDetails $_ }
    try {
        $DefenderPath = Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe'
        $Diagnostics.defender_command = @{ path = $DefenderPath; exists = Test-Path -LiteralPath $DefenderPath }
        if (Test-Path -LiteralPath $DefenderPath) {
            $Diagnostics.defender_command.version = (Get-Item -LiteralPath $DefenderPath).VersionInfo.FileVersion
            $Diagnostics.defender_command.signature = Get-SignatureRecord $DefenderPath
        }
    } catch { $Diagnostics.defender_command_error = Get-FailureDetails $_ }
    Save-Diagnostics
    $Os = $null
    $WmiError = $null
    try { $Os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop } catch { $WmiError = $_.Exception.Message }
    $RegistryOs = $null
    try { $RegistryOs = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop } catch { }
    $Tools = foreach ($ToolName in @('node', 'npm', 'python', 'python3', 'py', 'java', 'javac', 'ffmpeg', 'dotnet', 'git')) {
        $Commands = @(Get-Command $ToolName -CommandType Application -ErrorAction SilentlyContinue)
        [pscustomobject]@{ name = $ToolName; paths = @($Commands | ForEach-Object { $_.Source });
            installed_runtime_found = @($Commands | Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' }).Count -gt 0 }
    }
    $Run.inventory = [ordered]@{ os = [Environment]::OSVersion.VersionString; version = [Environment]::OSVersion.Version.ToString();
        edition = $RegistryOs.ProductName; build = $RegistryOs.CurrentBuild; architecture = $env:PROCESSOR_ARCHITECTURE; wmi_available = [bool]$Os; wmi_error = $WmiError;
        username = $env:USERNAME; powershell = $PSVersionTable.PSVersion.ToString(); tools = @($Tools);
        no_development_tools_on_path = @($Tools | Where-Object installed_runtime_found).Count -eq 0 }
    try {
        $Defender = Get-MpComputerStatus -ErrorAction Stop
        $Run.inventory.defender = @{ query_available = $true; antivirus_enabled = $Defender.AntivirusEnabled;
            real_time_protection_enabled = $Defender.RealTimeProtectionEnabled; signature_version = $Defender.AntivirusSignatureVersion;
            running_mode = $Defender.AMRunningMode; service_enabled = $Defender.AMServiceEnabled;
            engine_version = $Defender.AMEngineVersion; product_version = $Defender.AMProductVersion;
            signature_updated_at = [string]$Defender.AntivirusSignatureLastUpdated }
    } catch {
        $Details = Get-FailureDetails $_
        $Run.inventory.defender = @{ query_available = $false; error = $_.Exception.Message; details = $Details;
            failure_class = Get-FailureClass $Details $_.Exception.Message 'defender_status' }
    }
    Set-TestPhase $Run 'defender_capability'
    $Run.defender_probe = [ordered]@{ passed = $false; security_policy_changed = $false }
    if ($Diagnostics.defender_service -and [string]$Diagnostics.defender_service.Status -eq 'Stopped') {
        $Run.defender_probe.failure_class = 'defender_unavailable'
        $Run.defender_probe.reason = 'WinDefend service is stopped; harness does not start services or change policy'
    } elseif (-not (Test-Path -LiteralPath $DefenderPath)) {
        $Run.defender_probe.failure_class = 'defender_unavailable'
        $Run.defender_probe.reason = 'Windows Defender MpCmdRun.exe is missing'
    } else {
        $ProbeRoot = Join-Path $env:LOCALAPPDATA 'RedLotusSandboxAcceptance'
        New-Item -ItemType Directory -Path $ProbeRoot -Force | Out-Null
        $HarmlessFile = Join-Path $ProbeRoot 'defender-harmless.txt'
        'Red Lotus harmless local acceptance fixture.' | Set-Content -LiteralPath $HarmlessFile -Encoding ASCII
        try {
            $DefenderProcess = Start-TestProcess -FilePath $DefenderPath -ArgumentList @('-Scan', '-ScanType', '3', '-File', ('"' + $HarmlessFile + '"')) -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $DiagnosticRoot 'defender-scan-stdout.log') -RedirectStandardError (Join-Path $DiagnosticRoot 'defender-scan-stderr.log')
            $Run.defender_probe.exit_code = Wait-TestProcess $DefenderProcess 60000 'Defender scan'
            $Run.defender_probe.passed = $Run.defender_probe.exit_code -eq 0
            if (-not $Run.defender_probe.passed) { $Run.defender_probe.failure_class = 'defender_probe_failed' }
        } catch {
            $Run.defender_probe.error = Get-FailureDetails $_
            $Run.defender_probe.failure_class = Get-FailureClass $Run.defender_probe.error $_.Exception.Message 'defender_scan'
        }
    }
    Save-Result
    $Manifest = Get-Content -LiteralPath (Join-Path $InputRoot 'manifest.json') -Raw | ConvertFrom-Json
    if ($Manifest.schema -ne 2 -or $Manifest.version -notmatch '^\d+\.\d+\.\d+$' -or $Manifest.architecture -cne 'x64' -or @($Manifest.products).Count -ne 2 -or (@($Manifest.products.product | Sort-Object) -join ',') -cne 'bridge,player') { throw 'Invalid explicit product manifest' }
    if ((Get-FileHash -LiteralPath $PSCommandPath).Hash.ToLowerInvariant() -cne $Manifest.guest_script_sha256) { throw 'Guest script hash differs from the prepared manifest' }
    $Run.version = $Manifest.version
    $Run.manifest_sha256 = (Get-FileHash -LiteralPath (Join-Path $InputRoot 'manifest.json')).Hash.ToLowerInvariant()
    foreach ($Product in $Manifest.products) {
        foreach ($Leaf in @($Product.file, $Product.executable, $Product.data_name)) {
            if ([string]::IsNullOrWhiteSpace($Leaf) -or $Leaf -match '[\\/:<>"|?*\x00-\x1f]' -or $Leaf -in @('.', '..')) { throw 'Unsafe product manifest path' }
        }
        if ($Product.version -cne $Manifest.version -or $Product.sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid product version or hash' }
        $Row = [ordered]@{ product = $Product.product; version = $Product.version; passed = $false; phase = 'mapped_hash'; runtime_signatures = @() }
        $Run.products += $Row
        $Run.phase = "testing_$($Product.product)"
        $Smoke = $null
        $Install = $null
        $Installed = $false
        $InstallDirectory = Join-Path $env:LOCALAPPDATA ("RedLotusSandboxAcceptance\" + $Product.product + '\app')
        try {
            Set-TestPhase $Row 'mapped_hash'
            $MappedInstaller = Join-Path $InputRoot $Product.file
            $Row.mapped_installer_path = $MappedInstaller
            $Row.installer_sha256 = (Get-FileHash -LiteralPath $MappedInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($Row.installer_sha256 -ne $Product.sha256) { throw 'Mapped installer hash differs from the approved final artifact' }
            Set-TestPhase $Row 'guest_local_copy'
            $LocalInstallerRoot = Join-Path $env:LOCALAPPDATA ('RedLotusSandboxAcceptance\' + $Product.product + '\installer')
            New-Item -ItemType Directory -Path $LocalInstallerRoot -Force | Out-Null
            $Installer = Join-Path $LocalInstallerRoot $Product.file
            Copy-Item -LiteralPath $MappedInstaller -Destination $Installer
            $Row.installer_path = $Installer
            $Row.local_installer_sha256 = (Get-FileHash -LiteralPath $Installer).Hash.ToLowerInvariant()
            if ($Row.local_installer_sha256 -cne $Product.sha256) { throw 'Guest-local installer hash mismatch' }
            $Row.signature = Get-SignatureRecord $Installer
            Set-TestPhase $Row 'install_launch'
            $Row.install_attempt_at = [DateTime]::UtcNow.ToString('o')
            Save-Result
            $Install = Start-TestProcess -FilePath $Installer -ArgumentList @('/S', "/D=$InstallDirectory") -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $ResultRoot "$($Product.product)-install-stdout.log") -RedirectStandardError (Join-Path $ResultRoot "$($Product.product)-install-stderr.log")
            Set-TestPhase $Row 'install_wait'
            $Row.install_exit_code = Wait-TestProcess $Install 120000 'Install'
            if ($Row.install_exit_code -ne 0) { throw "Installer exited $($Row.install_exit_code)" }
            $Installed = $true
            $AppPath = Join-Path $InstallDirectory $Product.executable
            if (-not (Test-Path -LiteralPath $AppPath)) { throw 'Expected installed executable is missing' }
            $Row.app_sha256 = (Get-FileHash -LiteralPath $AppPath).Hash.ToLowerInvariant()
            $Row.app_signature = Get-SignatureRecord $AppPath
            $Row.phase = 'bundled_runtime_probes'
            Save-Result
            $ResourceRoot = Join-Path $InstallDirectory 'resources'
            $Row.runtime_probes = @(
                Invoke-RuntimeProbe 'python-fastapi' (Join-Path $ResourceRoot 'python\python.exe') @('-I', '-c', '"import fastapi; print(fastapi.__version__)"') 0
                Invoke-RuntimeProbe 'ffmpeg' (Join-Path $ResourceRoot 'ffmpeg\ffmpeg.exe') @('-version') 0
                Invoke-RuntimeProbe 'helper' (Join-Path $ResourceRoot 'scanner-helper\RedLotus.ScanHelper.exe') @('--invalid-smoke-arguments') 1
            )
            if ($Product.product -eq 'bridge') {
                $Row.runtime_probes += Invoke-RuntimeProbe 'java' (Join-Path $ResourceRoot 'jre\bin\java.exe') @('-version') 0
            } elseif (Test-Path -LiteralPath (Join-Path $ResourceRoot 'jre')) { throw 'Player unexpectedly contains a Java runtime' }
            Set-TestPhase $Row 'smoke_launch'
            $Smoke = Start-TestProcess -FilePath $AppPath -ArgumentList '--smoke' -PassThru -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $ResultRoot "$($Product.product)-stdout.log") `
                -RedirectStandardError (Join-Path $ResultRoot "$($Product.product)-stderr.log")
            Set-TestPhase $Row 'smoke_wait'
            $Row.smoke_exit_code = Wait-TestProcess $Smoke 120000 'Application smoke'
            $Log = Join-Path $env:LOCALAPPDATA ($Product.data_name + '\redlotus.log')
            if (Test-Path -LiteralPath $Log) { Copy-Item -LiteralPath $Log -Destination (Join-Path $ResultRoot "$($Product.product)-redlotus.log") }
            if ($Row.smoke_exit_code -ne 0 -or -not (Test-Path -LiteralPath $Log) -or -not (Select-String -LiteralPath $Log -Pattern 'SMOKE OK' -Quiet)) {
                throw 'Installed app did not prove authenticated startup, renderer isolation and graceful smoke shutdown'
            }
            $Row.smoke_passed = $true
        } catch { $Row.error = $_.Exception.Message; $Row.failure_phase = $Row.phase; $Row.failed_at = [DateTime]::UtcNow.ToString('o'); $Row.error_details = Get-FailureDetails $_; $Row.failure_class = Get-FailureClass $Row.error_details $Row.error $Row.phase }
        finally {
            if ($Install -and -not $Install.HasExited) { Stop-Process -Id $Install.Id -Force -ErrorAction SilentlyContinue; $Row.install_timed_out = $true }
            if ($Smoke -and -not $Smoke.HasExited) {
                # Only this test's guest process is terminated after its smoke deadline.
                Stop-Process -Id $Smoke.Id -Force -ErrorAction SilentlyContinue
                $Row.smoke_timed_out = $true
            }
            $Log = Join-Path $env:LOCALAPPDATA ($Product.data_name + '\redlotus.log')
            if (Test-Path -LiteralPath $Log) { Copy-Item -LiteralPath $Log -Destination (Join-Path $ResultRoot "$($Product.product)-redlotus.log") }
            if ($Installed) {
                try {
                    Set-TestPhase $Row 'uninstall_launch'
                    $Uninstallers = @(Get-ChildItem -LiteralPath $InstallDirectory -Filter 'Uninstall*.exe' -File)
                    if ($Uninstallers.Count -ne 1) { throw 'Expected exactly one installed uninstaller' }
                    $Removal = Start-TestProcess -FilePath $Uninstallers[0].FullName -ArgumentList @('/S', "_?=$InstallDirectory") -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $ResultRoot "$($Product.product)-uninstall-stdout.log") -RedirectStandardError (Join-Path $ResultRoot "$($Product.product)-uninstall-stderr.log")
                    Set-TestPhase $Row 'uninstall_wait'
                    $Row.uninstall_exit_code = Wait-TestProcess $Removal 120000 'Uninstall'
                    if ($Row.uninstall_exit_code -ne 0) { throw "Uninstaller exited $($Row.uninstall_exit_code)" }
                    $Row.installed_executable_removed = -not (Test-Path -LiteralPath (Join-Path $InstallDirectory $Product.executable))
                    if (-not $Row.installed_executable_removed) { throw 'Installed executable remains after uninstall' }
                } catch { $Row.uninstall_error = $_.Exception.Message; $Row.uninstall_error_details = Get-FailureDetails $_; $Row.uninstall_failure_class = Get-FailureClass $Row.uninstall_error_details $Row.uninstall_error $Row.phase }
            }
            $Row.passed = [bool]($Row.smoke_passed -and $Row.installed_executable_removed -and -not $Row.error -and -not $Row.uninstall_error)
            $Row.phase = 'complete'
            Save-Result
        }
    }
    $Run.passed = $Run.products.Count -eq 2 -and @($Run.products | Where-Object { -not $_.passed }).Count -eq 0 -and $Run.inventory.no_development_tools_on_path -and $Run.defender_probe.passed
    $Run.phase = 'complete'
} catch { $Run.error = $_.Exception.Message; $Run.error_position = $_.InvocationInfo.PositionMessage; $Run.phase = 'failed' }
finally {
    $Run.phase_before_diagnostics = $Run.phase
    $Run.phase = 'collecting_diagnostics'
    Save-Result
    try {
        # Allow enforcement events to reach their logs before the final collection.
        Start-Sleep -Seconds 3
        Save-PolicyEvents 'after-installers'
    } catch { $Run.diagnostic_error = Get-FailureDetails $_ }
    $Run.phase = $Run.phase_before_diagnostics
    $Run.finished_at = [DateTime]::UtcNow.ToString('o')
    Save-Result
    # Set-Content closes each mapped result before the completion marker is written.
    'complete' | Set-Content -LiteralPath (Join-Path $ResultRoot 'complete.txt') -Encoding ASCII
    Start-Sleep -Seconds 2
    & "$env:SystemRoot\System32\shutdown.exe" /s /t 0
}
