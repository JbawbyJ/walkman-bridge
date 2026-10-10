'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const windows = process.platform === 'win32';
const shell = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const psQuote = value => "'" + value.replaceAll("'", "''") + "'";
const json = file => JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
function powershell(args, host = false) {
  // Host preparation uses the installed PowerShell 7 under its existing policy.
  // Windows PowerShell remains the guest runtime and isolated function test host.
  const hostShell = path.join(process.env.ProgramFiles || 'C:\\Program Files', 'PowerShell', '7', 'pwsh.exe');
  const env = { ...process.env, PATH: path.dirname(process.execPath) + path.delimiter + process.env.PATH };
  // A Node child of pwsh otherwise hands incompatible PowerShell 7 module paths
  // to Windows PowerShell. Let each selected runtime initialize its own defaults.
  delete env.PSModulePath;
  return spawnSync(host && fs.existsSync(hostShell) ? hostShell : shell, ['-NoProfile', '-NonInteractive', ...args], {
    encoding: 'utf8', windowsHide: true, timeout: 60000,
    env,
  });
}
function fixture(t) {
  const parent = path.join(__dirname, 'build', 'sandbox-harness-tests');
  fs.mkdirSync(parent, { recursive: true });
  const root = fs.mkdtempSync(path.join(parent, 'explicit & local-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(root)), path.resolve(parent));
    fs.rmSync(root, { recursive: true });
  });
  fs.mkdirSync(path.join(root, 'packaging'));
  for (const file of ['products.cjs', 'windows-sandbox-smoke.ps1', 'windows-sandbox-guest.ps1']) {
    fs.copyFileSync(path.join(__dirname, file), path.join(root, 'packaging', file));
  }
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ version: '0.4.1' }));
  const hashes = {};
  for (const [key, prefix] of [['bridge', 'Walkman-Bridge'], ['player', 'Red-Lotus-Player']]) {
    const dir = path.join(root, 'dist_electron', key);
    fs.mkdirSync(dir, { recursive: true });
    const bytes = Buffer.from('harmless non-executable harness fixture ' + key);
    hashes[key] = hash(bytes);
    fs.writeFileSync(path.join(dir, prefix + '-Setup-0.4.1-x64.exe'), bytes);
    fs.writeFileSync(path.join(dir, prefix + '-Setup-99.0.0-x64.exe'), 'newer decoy');
  }
  return { root, hashes };
}
function prepare(f, options = {}) {
  return powershell(['-File', path.join(f.root, 'packaging', 'windows-sandbox-smoke.ps1'),
    '-Version', options.version || '0.4.1', '-BridgeSha256', options.bridgeHash || f.hashes.bridge,
    '-PlayerSha256', f.hashes.player, '-PrepareOnly'], true);
}
function guestFunctions(names, body) {
  return powershell(['-Command', `$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null;
    $ast=[System.Management.Automation.Language.Parser]::ParseFile(${psQuote(path.join(__dirname, 'windows-sandbox-guest.ps1'))},[ref]$tokens,[ref]$errors);
    if ($errors.Count) { throw ($errors | Out-String) };
    $names=@(${names.map(psQuote).join(',')});
    $ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in $names},$true) | ForEach-Object { Invoke-Expression $_.Extent.Text };
    ${body}`]);
}
test('Sandbox prepares only explicit current product artifacts with verified staged hashes', { skip: !windows }, t => {
  const f = fixture(t), result = prepare(f);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  const runRoot = result.stdout.trim();
  const manifest = json(path.join(runRoot, 'input', 'manifest.json'));
  assert.equal(manifest.version, '0.4.1');
  assert.deepEqual(manifest.products.map(p => p.file), ['Walkman-Bridge-Setup-0.4.1-x64.exe', 'Red-Lotus-Player-Setup-0.4.1-x64.exe']);
  assert.deepEqual(manifest.products.map(p => p.executable), ['Walkman Bridge.exe', 'Red Lotus Player.exe']);
  for (const p of manifest.products) assert.equal(hash(fs.readFileSync(path.join(runRoot, 'input', p.file))), f.hashes[p.product]);
  assert.equal(manifest.guest_script_sha256, hash(fs.readFileSync(path.join(runRoot, 'input', 'guest.ps1'))));
  assert.equal(json(path.join(runRoot, 'host-result.json')).launched, false);
  const xml = fs.readFileSync(path.join(runRoot, 'red-lotus-clean-install.wsb'), 'utf8');
  assert.match(xml, /RedLotusInput/);
  assert.match(xml, /RedLotusResults/);
  assert.match(xml, /&amp;/);
  assert.doesNotMatch(xml, /(?:^|\s)-ExecutionPolicy\s|Unblock-File|RunAs|NightOpsInput/i);
  assert.match(xml, /<Networking>Disable<\/Networking>/);
  assert.match(xml, /<ReadOnly>true<\/ReadOnly>/);
});
test('Sandbox refuses stale version, wrong hash, or missing exact artifact despite newer decoy', { skip: !windows }, t => {
  const f = fixture(t);
  let result = prepare(f, { version: '0.2.0' });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /differs from current package version/);
  result = prepare(f, { bridgeHash: '0'.repeat(64) });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /installer hash mismatch/);
  fs.unlinkSync(path.join(f.root, 'dist_electron', 'bridge', 'Walkman-Bridge-Setup-0.4.1-x64.exe'));
  result = prepare(f);
  assert.notEqual(result.status, 0);
  assert.equal(fs.existsSync(path.join(f.root, 'packaging', 'build', 'windows-sandbox')), false);
});
test('Sandbox rejects product path traversal before staging', { skip: !windows }, t => {
  const f = fixture(t);
  const config = path.join(f.root, 'packaging', 'products.cjs');
  fs.writeFileSync(config, fs.readFileSync(config, 'utf8').replace('Walkman-Bridge-Setup-', '../Walkman-Bridge-Setup-'));
  const result = prepare(f);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /unsafe or unresolved file name/);
});
test('XML bootstrap records policy and script-launch result under the unchanged Windows PowerShell policy', { skip: !windows }, t => {
  const f = fixture(t), prepared = prepare(f);
  assert.equal(prepared.status, 0, prepared.stderr);
  const runRoot = prepared.stdout.trim();
  const input = path.join(runRoot, 'input'), results = path.join(runRoot, 'results');
  // Never execute the production guest body on the host: replace this private
  // test copy with a harmless marker script before trying normal -File startup.
  fs.writeFileSync(path.join(input, 'guest.ps1'),
    "@{test_fixture=$true} | ConvertTo-Json | Set-Content -LiteralPath " + psQuote(path.join(results, 'guest-result.json')));
  const xml = fs.readFileSync(path.join(runRoot, 'red-lotus-clean-install.wsb'), 'utf8');
  let command = xml.match(/<Command>([\s\S]*?)<\/Command>/)[1]
    .replaceAll('&quot;', '"').replaceAll('&apos;', "'").replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&amp;', '&');
  command = command.slice(command.indexOf('-Command "') + 10, -1)
    .replaceAll('C:\\RedLotusResults', results).replaceAll('C:\\RedLotusInput\\guest.ps1', psQuote(path.join(input, 'guest.ps1')))
    .replaceAll('C:\\RedLotusInput', input);
  const executed = powershell(['-Command', command]);
  assert.equal(executed.error, undefined);
  assert.ok(fs.existsSync(path.join(results, 'bootstrap.json')), executed.stderr || executed.stdout);
  const bootstrap = json(path.join(results, 'bootstrap.json'));
  const exit = json(path.join(results, 'bootstrap-exit.json'));
  assert.equal(bootstrap.policy_changes, false);
  assert.equal(typeof exit.exit_code, 'number');
  if (bootstrap.effective_execution_policy === 'Restricted') {
    assert.equal(exit.failure_class, 'execution_policy_block');
    assert.equal(exit.guest_result_present, false);
    assert.ok(fs.existsSync(path.join(results, 'bootstrap-events.json')));
  } else if (exit.guest_result_present) {
    assert.equal(json(path.join(results, 'guest-result.json')).test_fixture, true);
  } else {
    assert.ok(['execution_policy_block', 'guest_script_not_started'].includes(exit.failure_class));
  }
});
test('guest separates application control, token elevation, permission, and unknown Defender probe failures', { skip: !windows }, () => {
  const result = guestFunctions(['Get-FailureClass'], `
    $rows=@(); foreach ($case in @(@(5,'permission_denied'),@(740,'elevation_required'),@(1260,'application_control_block'),@(577,'application_control_block'))) {
      $actual=Get-FailureClass @{exceptions=@(@{native_error_code=$case[0];message='opaque failure'})} '' 'install_launch';
      if ($actual -ne $case[1]) { throw "classification $actual" }; $rows += $actual
    };
    if ((Get-FailureClass @{exceptions=@()} 'An Application Control policy has blocked this file.' 'install_launch') -ne 'application_control_block') { throw 'message classification' };
    if ((Get-FailureClass @{exceptions=@()} 'Cannot connect to CIM server. Access is denied.' 'defender_status') -ne 'permission_denied') { throw 'CIM denial misclassified' };
    if ((Get-FailureClass @{exceptions=@()} 'unknown' 'defender_scan') -ne 'defender_probe_failed') { throw 'probe overclaimed unavailable' };
    $rows | ConvertTo-Json`);
  assert.equal(result.status, 0, result.stderr || result.stdout);
});
test('guest retained process runner captures real fast zero and nonzero exits and kills its timed out child', { skip: !windows }, () => {
  const result = guestFunctions(['Start-TestProcess', 'Wait-TestProcess'], `
    foreach ($code in @(0,7,0,7,0,7)) {
      $p=Start-TestProcess -FilePath "$env:SystemRoot\\System32\\cmd.exe" -ArgumentList @('/d','/c',"exit $code") -PassThru -WindowStyle Hidden;
      $actual=Wait-TestProcess $p 5000 'test';
      if ($null -eq $actual -or $actual -ne $code) { throw "bad exit code $actual expected $code" }
    };
    $p=Start-TestProcess -FilePath "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -ArgumentList @('-NoProfile','-NonInteractive','-Command','"Start-Sleep -Seconds 10"');
    $caught=$false; try { Wait-TestProcess $p 25 'timeout-test' } catch { if ($_.Exception.Message -notlike '*exceeded 25 ms*') { throw }; $caught=$true };
    if (-not $caught -or -not $p.WaitForExit(5000)) { throw 'timeout child was not terminated' };
    'passed'`);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /passed/);
});
test('guest source keeps local-copy launch, diagnostic phases, and current names without security policy mutations', () => {
  const source = fs.readFileSync(path.join(__dirname, 'windows-sandbox-guest.ps1'), 'utf8');
  assert.match(source, /\$Installer = Join-Path \$LocalInstallerRoot \$Product.file/);
  assert.match(source, /local_installer_sha256 -cne \$Product.sha256/);
  assert.match(source, /Start-TestProcess -FilePath \$Installer/);
  for (const phase of ['mapped_hash', 'guest_local_copy', 'install_launch', 'install_wait', 'smoke_launch', 'smoke_wait', 'uninstall_launch', 'uninstall_wait']) assert.ok(source.includes("'" + phase + "'"));
  assert.match(source, /RedLotus\.ScanHelper\.exe/);
  assert.match(source, /redlotus\.log/);
  assert.match(source, /defender_unavailable/);
  assert.doesNotMatch(source, /Set-ExecutionPolicy|Set-MpPreference|Add-MpPreference|Unblock-File|-Verb\s+RunAs|Start-Service|Stop-Service/);
});
