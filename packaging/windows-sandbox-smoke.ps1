[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Fa-f0-9]{64}$')][string]$BridgeSha256,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Fa-f0-9]{64}$')][string]$PlayerSha256,
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
# Resolve exact current build definitions, never a newest-file search.
$Node = @(Get-Command node.exe -CommandType Application -ErrorAction Stop)[0].Source
$ConfigJson = & $Node -e 'const p=require(process.argv[1]);const v=require(process.argv[2]).version;process.stdout.write(JSON.stringify({version:v,products:p.products}));' (Join-Path $PSScriptRoot 'products.cjs') (Join-Path $Repo 'package.json')
if ($LASTEXITCODE -ne 0) { throw 'Could not read the current product configuration' }
$Config = $ConfigJson | ConvertFrom-Json
if ($Config.version -cne $Version) { throw "Requested version $Version differs from current package version $($Config.version)" }
$ExpectedHashes = @{ bridge = $BridgeSha256.ToLowerInvariant(); player = $PlayerSha256.ToLowerInvariant() }
$Products = foreach ($Key in @('bridge', 'player')) {
    $Definition = $Config.products.$Key
    if (-not $Definition) { throw "Current product configuration lacks $Key" }
    $File = $Definition.artifactName.Replace('${version}', $Version).Replace('${arch}', 'x64').Replace('${ext}', 'exe')
    $Executable = $Definition.executableName + '.exe'
    foreach ($Leaf in @($File, $Executable, $Definition.productName)) {
        if ([string]::IsNullOrWhiteSpace($Leaf) -or $Leaf -match '[\\/:<>"|?*\x00-\x1f]' -or $Leaf -in @('.', '..') -or $Leaf.Contains('${')) { throw 'Product configuration contains an unsafe or unresolved file name' }
    }
    $Source = Join-Path $Repo ('dist_electron\' + $Key + '\' + $File)
    $Hash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Hash -cne $ExpectedHashes[$Key]) { throw "Final $Key installer hash mismatch" }
    [ordered]@{ product = $Key; version = $Version; file = $File; executable = $Executable;
        data_name = $Definition.productName; sha256 = $Hash; source = $Source }
}
# Only allocate evidence after both explicitly selected artifacts have passed.
$RunRoot = Join-Path $Repo ('packaging\build\windows-sandbox\' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
$InputRoot = Join-Path $RunRoot 'input'
$ResultRoot = Join-Path $RunRoot 'results'
New-Item -ItemType Directory -Path $InputRoot,$ResultRoot -Force | Out-Null
foreach ($Product in $Products) {
    $Destination = Join-Path $InputRoot $Product.file
    Copy-Item -LiteralPath $Product.source -Destination $Destination
    if ((Get-FileHash -LiteralPath $Destination).Hash.ToLowerInvariant() -cne $Product.sha256) { throw 'Staged installer hash mismatch' }
    $Product.Remove('source')
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'windows-sandbox-guest.ps1') -Destination (Join-Path $InputRoot 'guest.ps1')
$Manifest = [ordered]@{ schema = 2; version = $Version; architecture = 'x64'; products = @($Products);
    product_config_sha256 = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'products.cjs')).Hash.ToLowerInvariant();
    guest_script_sha256 = (Get-FileHash -LiteralPath (Join-Path $InputRoot 'guest.ps1')).Hash.ToLowerInvariant() }
$Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $InputRoot 'manifest.json') -Encoding UTF8
$InputXml = [System.Security.SecurityElement]::Escape($InputRoot)
$OutputXml = [System.Security.SecurityElement]::Escape($ResultRoot)
# The inline bootstrap only records policy and invokes a normal -File child. It does
# not reinterpret guest.ps1, elevate, unblock files, or override execution policy.
$Bootstrap = @'
& {
  $r='C:\RedLotusResults'; $start=[DateTime]::UtcNow;
  [ordered]@{started_at=$start.ToString('o');effective_execution_policy=[string](Get-ExecutionPolicy);execution_policy=@(Get-ExecutionPolicy -List | ForEach-Object { @{scope=[string]$_.Scope;policy=[string]$_.ExecutionPolicy} });language_mode=[string]$ExecutionContext.SessionState.LanguageMode;policy_changes=$false} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath ($r+'\bootstrap.json') -Encoding UTF8;
  & C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -File C:\RedLotusInput\guest.ps1 *> ($r+'\guest-console.log');
  $code=$LASTEXITCODE; $present=Test-Path -LiteralPath ($r+'\guest-result.json');
  $exit=[ordered]@{finished_at=[DateTime]::UtcNow.ToString('o');exit_code=$code;guest_result_present=$present;passed=$false};
  if (-not $present) {
    $exit.failure_phase='guest_script_launch'; $exit.failure_class='guest_script_not_started';
    if (Select-String -LiteralPath ($r+'\guest-console.log') -Pattern 'running scripts is|about_Execution_Policies|not digitally signed' -Quiet) { $exit.failure_class='execution_policy_block' };
    & C:\Windows\System32\whoami.exe /all *> ($r+'\bootstrap-token.log');
    & C:\Windows\System32\sc.exe query WinDefend *> ($r+'\bootstrap-defender-service.log');
    & C:\Windows\System32\CiTool.exe -lp -json *> ($r+'\bootstrap-app-control-policies.log');
    $installers=@(); try {
      $manifest=Get-Content -LiteralPath 'C:\RedLotusInput\manifest.json' -Raw | ConvertFrom-Json;
      foreach ($product in $manifest.products) {
        if ($product.file -match '[\\/:]' -or $product.file -in @('.','..')) { throw 'Unsafe manifest leaf' };
        $file=Join-Path 'C:\RedLotusInput' $product.file; $signature=Get-AuthenticodeSignature -LiteralPath $file;
        $installers+=@{product=$product.product;version=$manifest.version;file=$product.file;sha256=(Get-FileHash -LiteralPath $file).Hash;signature_status=[string]$signature.Status;signer=$signature.SignerCertificate.Subject;thumbprint=$signature.SignerCertificate.Thumbprint}
      }
    } catch { $installers+=@{query_error=$_.Exception.Message;error_id=$_.FullyQualifiedErrorId} };
    $installers | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath ($r+'\bootstrap-installers.json') -Encoding UTF8;
    $events=@(); foreach ($channel in @('Microsoft-Windows-CodeIntegrity/Operational','Microsoft-Windows-AppLocker/MSI and Script')) {
      try { $events+=@(Get-WinEvent -FilterHashtable @{LogName=$channel;StartTime=$start} -MaxEvents 100 -ErrorAction Stop | ForEach-Object { @{channel=$channel;id=$_.Id;record_id=$_.RecordId;at=$_.TimeCreated.ToUniversalTime().ToString('o');xml=$_.ToXml()} }) }
      catch {
        if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $events+=@{channel=$channel;available=$true;count=0} }
        else { $events+=@{channel=$channel;available=$false;query_error=$_.Exception.Message;error_id=$_.FullyQualifiedErrorId} }
      }
    }; $events | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath ($r+'\bootstrap-events.json') -Encoding UTF8;
    'guest_script_not_started' | Set-Content -LiteralPath ($r+'\complete.txt') -Encoding ASCII
  }; $exit | ConvertTo-Json | Set-Content -LiteralPath ($r+'\bootstrap-exit.json') -Encoding UTF8
}
'@
$Command = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -Command "' + $Bootstrap.Trim() + '"'
$CommandXml = [System.Security.SecurityElement]::Escape($Command)
$ConfigPath = Join-Path $RunRoot 'red-lotus-clean-install.wsb'
@"
<Configuration>
  <Networking>Disable</Networking><vGPU>Disable</vGPU>
  <ClipboardRedirection>Disable</ClipboardRedirection><AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput><PrinterRedirection>Disable</PrinterRedirection>
  <MemoryInMB>4096</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$InputXml</HostFolder><SandboxFolder>C:\RedLotusInput</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$OutputXml</HostFolder><SandboxFolder>C:\RedLotusResults</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>$CommandXml</Command></LogonCommand>
</Configuration>
"@ | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
$HostResult = [ordered]@{ prepared_at = [DateTime]::UtcNow.ToString('o'); version = $Version; configuration = $ConfigPath;
    input = $InputRoot; results = $ResultRoot; installer_hashes_verified = $true; products = @($Products); launched = $false;
    manifest_sha256 = (Get-FileHash -LiteralPath (Join-Path $InputRoot 'manifest.json')).Hash.ToLowerInvariant();
    policy_changes = $false; effective_execution_policy = [string](Get-ExecutionPolicy); execution_policy = @(Get-ExecutionPolicy -List | ForEach-Object { @{ scope = [string]$_.Scope; policy = [string]$_.ExecutionPolicy } });
    powershell_language_mode = [string]$ExecutionContext.SessionState.LanguageMode }
$HostResultPath = Join-Path $RunRoot 'host-result.json'
$HostResult | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $HostResultPath -Encoding UTF8
if (-not $PrepareOnly) {
    try {
        $Sandbox = Start-Process -FilePath "$env:SystemRoot\System32\WindowsSandbox.exe" -ArgumentList ('"' + $ConfigPath + '"') -WindowStyle Hidden -PassThru
        $HostResult.launched = $true
        $HostResult.launch_pid = $Sandbox.Id
        $HostResult.launched_at = [DateTime]::UtcNow.ToString('o')
    } catch { $HostResult.launch_error = $_.Exception.Message; $HostResult.launch_error_id = $_.FullyQualifiedErrorId; throw }
    finally { $HostResult | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $HostResultPath -Encoding UTF8 }
}
Write-Output $RunRoot
