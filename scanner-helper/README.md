# Native scanner exchange

The same self-contained executable runs in two modes. Electron launches the
unelevated `--exchange --pipe NAME --cache-root ROOT` mode with sanitized runtime
environment variables. This native process launches the elevated scanner with
`RunAs` and retains its process handle until the exchange completes.

Before reporting the elevated PID to Electron, the exchange opens the private
named pipe and calls Windows `GetNamedPipeServerProcessId`. Electron then claims
the pending requests with the backend and sends their secrets over the exchange
process's private stdin. The exchange verifies the pipe server PID again before
writing any nonce or file identity. The elevated pipe server independently checks
the exchange client PID using `GetNamedPipeClientProcessId`.

Private stdin and pipe payload: `{ "requests": [ScanRequest, ...] }`.
Result: `{ "results": [ScanResponse, ...] }`. Each request includes the existing
request ID, nonce, media ID, managed path, SHA-256, size, and expected helper PID.
Batches contain 1–200 unique requests. Messages are bounded to 2 MiB and native
operations to 30 minutes. Backend claim/result/cancel endpoints remain per-file.
Any missing, duplicated, malformed, or mismatched batch result is rejected before
the broker submits a clean result. Cancellation does not grant clearance.

Elevated Defender status uses the OS System directory's absolute Windows
PowerShell executable and explicitly imports the trusted Defender and Utility
module manifests. PowerShell module auto-loading is disabled, its working
directory is trusted, and child environments discard .NET hooks, profiler
variables, and caller module paths. The published runtime disables startup-hook
support; the actual single-file artifact is checked with a harmless hook DLL.

Build with the SDK version in `global.json`. Workspace SDK provisioning used the
official [Microsoft 10.0.402 win-x64 ZIP](https://builds.dotnet.microsoft.com/dotnet/Sdk/10.0.402/dotnet-sdk-10.0.402-win-x64.zip)
and verified its published SHA-512 against the
[.NET 10 release metadata](https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json):
`ae4fd528cb7e9f6af6aaf7de36715a69f761f183744f1bb3025d19f5a92447090ec561410e46266c1d10a63781ed6f3de771760bc76c876d87daabc367b898cd`.
This SDK publishes the helper with the self-contained .NET 10.0.13 runtime.

Verification commands (from this directory), with `$Dotnet` set to the verified
portable SDK's `dotnet.exe` path:

```
& $Dotnet build ScannerHelper.Tests/ScannerHelper.Tests.csproj -c Release --artifacts-path artifacts/build
& $Dotnet publish ScannerHelper/ScannerHelper.csproj -c Release --artifacts-path artifacts/build -o artifacts/win-x64
artifacts/build/bin/ScannerHelper.Tests/release_win-x64/ScannerHelper.Tests.exe
powershell -File tests/Verify-PublishedHardening.ps1
node ../electron/scanner-broker.test.cjs
```

The opt-in `node tests/real-uac-smoke.cjs` requests real elevation for two generated
silent WAV files and verifies their actual Defender results. Its backend is an
isolated test fixture; it never accesses a Walkman. Cancellation is recorded as
such and must not be reported as a successful elevated scan.
