[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('bridge','player')][string]$Product)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$CurrentName = if ($Product -eq 'bridge') { 'Walkman Bridge' } else { 'Red Lotus Player' }
$OldName = if ($Product -eq 'bridge') { 'Walkman Bridge ' + [char]0x2014 + ' Night Ops' } else { 'Red Lotus Player' }
$OldFile = if ($Product -eq 'bridge') { 'Walkman-Bridge-Night-Ops-Setup-0.4.0-x64.exe' } else { 'Red-Lotus-Player-Setup-0.4.0-x64.exe' }
$NewFile = if ($Product -eq 'bridge') { 'Walkman-Bridge-Setup-0.4.1-x64.exe' } else { 'Red-Lotus-Player-Setup-0.4.1-x64.exe' }
$OldExe = if ($Product -eq 'bridge') { 'Walkman Bridge Night Ops.exe' } else { 'Red Lotus Player.exe' }
$NewExe = $CurrentName + '.exe'
function Registrations {
    $RegistryRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
    if (-not (Test-Path -LiteralPath $RegistryRoot -ErrorAction Stop)) { return @() }
    @(Get-ChildItem -LiteralPath $RegistryRoot -ErrorAction Stop | Get-ItemProperty -ErrorAction Stop | Where-Object { $_.DisplayName -in @($CurrentName,$OldName) })
}
if (@(Registrations).Count) { throw 'An existing user product is installed; isolated upgrade testing refuses to replace it.' }
if (Get-Process -Name $CurrentName,([IO.Path]::GetFileNameWithoutExtension($OldExe)) -ErrorAction SilentlyContinue) { throw 'A user product is running.' }
foreach ($Directory in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))) {
    foreach ($Name in @($CurrentName,$OldName)) {
        if (Test-Path -LiteralPath (Join-Path $Directory ($Name + '.lnk'))) { throw 'An existing user product shortcut is present; isolated testing refuses to replace it.' }
    }
}
$Run = Join-Path $Repo ('packaging/build/upgrade-041/' + $Product + '-' + [Guid]::NewGuid().ToString('N'))
$Install = Join-Path $Run 'installed'
$LocalData = Join-Path $Run 'local-data'
$RoamingData = Join-Path $Run 'roaming-data'
$Data = Join-Path $LocalData $CurrentName
New-Item -ItemType Directory -Path $Run,$LocalData,$RoamingData | Out-Null
$PreviousLocal = $env:LOCALAPPDATA; $PreviousRoaming = $env:APPDATA; $PreviousData = $env:REDLOTUS_UPGRADE_DIR
$PreviousProduct = $env:REDLOTUS_UPGRADE_PRODUCT; $PreviousNode = $env:ELECTRON_RUN_AS_NODE
$Installed = $false
$BodyPassed = $false
$Proof = [ordered]@{product=$Product;passed=$false;from_version='0.4.0';to_version='0.4.1';cleanup=@{passed=$false}}
function Run-Process([string]$File, [string[]]$Arguments) {
    $Process = Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    $null = $Process.Handle
    if (-not $Process.WaitForExit(180000)) { throw "Test process remains running for diagnosis: $($Process.Id)" }
    $Process.WaitForExit(); $Process.Refresh()
    if ($Process.ExitCode -ne 0) { throw "Test process failed: $File (exit $($Process.ExitCode))" }
}
$SeedCode = @'
import io,json,os,wave,hashlib
from pathlib import Path
from types import SimpleNamespace
from application import create_app
from jobs import JobStatus
data=Path(os.environ['REDLOTUS_UPGRADE_DIR'])
app=create_app(product=os.environ['REDLOTUS_UPGRADE_PRODUCT'],data_dir=data,token='upgrade-fixture-'+('x'*48),origin='http://127.0.0.1:48234',device_api=SimpleNamespace(find_walkman=lambda:None))
store=app.state.store
audio=io.BytesIO()
with wave.open(audio,'wb') as stream:
 stream.setparams((1,2,8000,0,'NONE','not compressed'));stream.writeframes(bytes(16000))
source=data/'original.wav';source.write_bytes(audio.getvalue())
record=store.import_file('Upgrade preserved.wav',io.BytesIO(audio.getvalue()))
playlist=store.create_playlist('Upgrade playlist',[record['id']])
state=store.playback_state(dict(media_id=record['id'],playlist_id=playlist['id'],position_seconds=0.5,volume=0.35,repeat='all',shuffle=False))
job=app.state.jobs.create('upgrade-terminal-history',1,kind='import');job.set_files([dict(file_id=record['id'],media_id=record['id'],name=record['name'],state='failed')],phase='finished');job.set_status(JobStatus.FAILED,'Preserved terminal history fixture')
expected=dict(queue=store.queue(),playlists=store.playlists(),playback=state,job=job.to_dict(),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),cache_sha256=record['artifacts']['source']['sha256'])
(data/'upgrade-expected.json').write_text(json.dumps(expected),encoding='utf-8')
app.state.jobs.close()
print('Seeded previous-version store, playlist, settings and terminal job without granting scan clearance')
'@
$CheckCode = @'
import json,os,hashlib
from pathlib import Path
from types import SimpleNamespace
from application import create_app,VERSION
assert VERSION=='0.4.1'
data=Path(os.environ['REDLOTUS_UPGRADE_DIR']);expected=json.loads((data/'upgrade-expected.json').read_text(encoding='utf-8'))
app=create_app(product=os.environ['REDLOTUS_UPGRADE_PRODUCT'],data_dir=data,token='upgrade-fixture-'+('x'*48),origin='http://127.0.0.1:48234',device_api=SimpleNamespace(find_walkman=lambda:None))
store=app.state.store
# The deliberately unscanned old import must recover as needs_scan, not queued.
# Every other persisted field remains identical; no synthetic clearance is issued.
assert expected['queue'][0]['status']=='queued'
expected['queue'][0]['status']='needs_scan'
assert store.queue()==expected['queue'];assert store.playlists()==expected['playlists'];assert store.playback_state()==expected['playback']
assert app.state.jobs.get('upgrade-terminal-history').to_dict()==expected['job']
assert hashlib.sha256((data/'original.wav').read_bytes()).hexdigest()==expected['source_sha256']
media_id=expected['queue'][0]['id'];assert hashlib.sha256((data/'cache'/media_id/'source.wav').read_bytes()).hexdigest()==expected['cache_sha256']
app.state.jobs.close();print('New installed runtime preserved previous data and media bytes')
'@
try {
    $env:LOCALAPPDATA = $LocalData; $env:APPDATA = $RoamingData; $env:REDLOTUS_UPGRADE_DIR = $Data; $env:REDLOTUS_UPGRADE_PRODUCT = $Product
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    $OldInstaller = Join-Path $Repo "dist_electron/$Product/$OldFile"
    $NewInstaller = Join-Path $Repo "dist_electron/$Product/$NewFile"
    Run-Process $OldInstaller @('/S', "/D=$Install")
    $Installed = $true
    $BeforeRegistration = @(Registrations)
    if ($BeforeRegistration.Count -ne 1) { throw 'Previous product did not create exactly one registration' }
    $RegistryId = $BeforeRegistration[0].PSChildName
    & (Join-Path $Install 'resources/python/python.exe') -I -B -c $SeedCode
    if ($LASTEXITCODE) { throw 'Previous-version seed failed' }
    Run-Process $NewInstaller @('/S', "/D=$Install")
    $Registration = @(Registrations)
    if ($Registration.Count -ne 1 -or $Registration[0].PSChildName -ne $RegistryId -or $Registration[0].DisplayName -ne $CurrentName -or $Registration[0].DisplayVersion -ne '0.4.1') { throw 'Upgrade changed identity, duplicated registration, or retained the old display name' }
    if (-not (Test-Path -LiteralPath (Join-Path $Install $NewExe))) { throw 'Renamed executable missing' }
    if ($Product -eq 'bridge' -and (Test-Path -LiteralPath (Join-Path $Install $OldExe))) { throw 'Old executable remains after upgrade' }
    & (Join-Path $Install 'resources/python/python.exe') -I -B -c $CheckCode
    if ($LASTEXITCODE) { throw 'Upgrade did not preserve stored user data' }
    $Shell = New-Object -ComObject WScript.Shell
    $ShortcutProof = @()
    foreach ($Directory in @([Environment]::GetFolderPath('Desktop'),(Join-Path $RoamingData 'Microsoft/Windows/Start Menu/Programs'))) {
        $Link = Join-Path $Directory ($CurrentName + '.lnk')
        if (Test-Path -LiteralPath $Link) {
            $Target = $Shell.CreateShortcut($Link).TargetPath
            if ($Target -ne (Join-Path $Install $NewExe)) { throw "Shortcut still points at old executable: $Link" }
            $ShortcutProof += @{path=$Link;target=$Target}
        }
        if ($Product -eq 'bridge' -and (Test-Path -LiteralPath (Join-Path $Directory ($OldName + '.lnk')))) { throw 'Old branded shortcut remains after upgrade' }
    }
    if (-not $ShortcutProof.Count) { throw 'No renamed product shortcut was found' }
    Run-Process (Join-Path $Install $NewExe) @('--smoke')
    $Log = Join-Path $Data 'redlotus.log'
    if (-not (Select-String -LiteralPath $Log -Pattern 'SMOKE OK' -Quiet)) { throw 'Renamed installed app did not pass authenticated startup/drain' }
    $Proof.registry_id=$RegistryId; $Proof.single_registration=$true
    $Proof.music_playlists_settings_history_preserved=$true; $Proof.pending_import_recovers_needs_scan=$true
    $Proof.shortcuts=$ShortcutProof; $Proof.installed_app_start_drain=$true
    $Proof.installer_sha256=(Get-FileHash -LiteralPath $NewInstaller).Hash; $Proof.log=$Log
    $BodyPassed = $true
} catch {
    $Proof.failure = $_.Exception.Message
    throw
} finally {
    try {
        if ($Installed) {
            if (Get-Process -Name $CurrentName,([IO.Path]::GetFileNameWithoutExtension($OldExe)) -ErrorAction SilentlyContinue) { throw 'Test product remains running; cleanup deferred for diagnosis.' }
            $Uninstaller = @(Get-ChildItem -LiteralPath $Install -Filter 'Uninstall*.exe' -File)
            if ($Uninstaller.Count -ne 1) { throw 'Exactly one test-owned uninstaller is required.' }
            Run-Process $Uninstaller[0].FullName @('/S',"_?=$Install")
            if (@(Registrations).Count) { throw 'Uninstall left a product registration.' }
            foreach ($Directory in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'),(Join-Path $RoamingData 'Microsoft/Windows/Start Menu/Programs'))) {
                foreach ($Name in @($CurrentName,$OldName)) {
                    if (Test-Path -LiteralPath (Join-Path $Directory ($Name + '.lnk'))) { throw 'Uninstall left a product shortcut.' }
                }
            }
            $Proof.cleanup = @{passed=$true;registration_removed=$true;shortcuts_removed=$true}
        }
    } catch {
        $Proof.cleanup = @{passed=$false;failure=$_.Exception.Message}
        throw
    } finally {
        $env:LOCALAPPDATA = $PreviousLocal; $env:APPDATA = $PreviousRoaming; $env:REDLOTUS_UPGRADE_DIR = $PreviousData
        $env:REDLOTUS_UPGRADE_PRODUCT = $PreviousProduct; $env:ELECTRON_RUN_AS_NODE = $PreviousNode
        $Proof.passed = $BodyPassed -and $Proof.cleanup.passed
        $Proof | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Run 'result.json') -Encoding utf8
    }
}
Write-Output "Upgrade PASS: $Product ($Run)"
