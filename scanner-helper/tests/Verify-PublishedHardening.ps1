$ErrorActionPreference = 'Stop'
$taskProofRoot = Join-Path $PSScriptRoot '../artifacts/hardening-proof-041'
New-Item -ItemType Directory -Path $taskProofRoot -Force | Out-Null
$taskHookPath = Join-Path $taskProofRoot ('HarmlessHook-' + [Guid]::NewGuid().ToString('N') + '.dll')
Add-Type -TypeDefinition 'public class StartupHook { public static void Initialize() { System.Console.WriteLine("NIGHTOPS_HARMLESS_STARTUP_HOOK_EXECUTED"); } }' -OutputAssembly $taskHookPath -OutputType Library
$taskExecutable = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../artifacts/win-x64/RedLotus.ScanHelper.exe')).Path
$taskStart = [Diagnostics.ProcessStartInfo]::new($taskExecutable)
$taskStart.Arguments = '--invalid-hardening-test'
$taskStart.UseShellExecute = $false
$taskStart.CreateNoWindow = $true
$taskStart.RedirectStandardOutput = $true
$taskStart.RedirectStandardError = $true
$taskStart.Environment['DOTNET_STARTUP_HOOKS'] = $taskHookPath
$taskProcess = [Diagnostics.Process]::Start($taskStart)
$taskStdout = $taskProcess.StandardOutput.ReadToEnd()
$taskStderr = $taskProcess.StandardError.ReadToEnd()
$taskProcess.WaitForExit()
$taskExit = $taskProcess.ExitCode
$taskProcess.Dispose()
$taskHookExecuted = $taskStdout.Contains('NIGHTOPS_HARMLESS_STARTUP_HOOK_EXECUTED')
if ($taskHookExecuted -or $taskExit -ne 1) { throw 'Published helper did not reject the injected startup hook and invalid request.' }
$taskProof = @{ ok = $true; helper_sha256 = (Get-FileHash -LiteralPath $taskExecutable -Algorithm SHA256).Hash; startup_hook_executed = $taskHookExecuted; invalid_request_exit_code = $taskExit; stderr = $taskStderr.Trim() }
$taskProof | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskProofRoot 'published-startup-hook.json') -Encoding utf8
$taskProof | ConvertTo-Json -Compress
Remove-Item -LiteralPath $taskHookPath
