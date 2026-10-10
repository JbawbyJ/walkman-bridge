using System.Security.Cryptography;
using System.Security;
using System.Diagnostics;
using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using NightOps.ScannerHelper;

var failures = new List<string>();
Run("managed path and request identity", TestManagedRequest);
Run("outside managed path rejected", TestOutsideRejected);
Run("fixed Defender command disables remediation", TestFixedCommand);
Run("clean result requires signature status", TestCleanRequiresStatus);
Run("clean result rejects disabled or passive Defender", TestDisabledDefender);
Run("trusted PowerShell and sanitized child environment", TestTrustedProcess);
Run("batch limits and duplicate identities reject", TestBatchLimits);
await RunAsync("wrong pipe server PID receives zero secret bytes", TestWrongServerPid);
await RunAsync("one-shot named pipe binds process and harmless Defender result", TestOneShotHelper);

if (failures.Count > 0)
{
    Console.Error.WriteLine(string.Join(Environment.NewLine, failures));
    return 1;
}
Console.WriteLine("ScannerHelper.Tests: 9 passed");
return 0;

void Run(string name, Action test)
{
    try { test(); }
    catch (Exception ex) { failures.Add($"FAIL {name}: {ex.Message}"); }
}

async Task RunAsync(string name, Func<Task> test)
{
    try { await test(); }
    catch (Exception ex) { failures.Add($"FAIL {name}: {ex.Message}"); }
}

void TestManagedRequest()
{
    using var fixture = Fixture.Create();
    var request = fixture.Request();
    using var opened = ManagedFile.OpenValidated(request.Path, fixture.Root);
    Assert(opened.SizeBytes == request.SizeBytes, "size must bind");
    Assert(opened.Sha256 == request.Sha256, "hash must bind");
    Protocol.ValidateRequest(request, Environment.ProcessId);
}

void TestOutsideRejected()
{
    using var fixture = Fixture.Create();
    var outside = Path.Combine(Path.GetDirectoryName(fixture.Root)!, "outside.mp3");
    File.WriteAllBytes(outside, [1, 2, 3]);
    try { AssertThrows<SecurityException>(() => ManagedFile.OpenValidated(outside, fixture.Root)); }
    finally { File.Delete(outside); }
}

void TestFixedCommand()
{
    var args = DefenderScanner.BuildScanArguments("C:\\managed cache\\song.mp3");
    Assert(args.SequenceEqual(["-Scan", "-ScanType", "3", "-File", "C:\\managed cache\\song.mp3", "-DisableRemediation"]), "unexpected Defender arguments");
}

void TestTrustedProcess()
{
    Assert(Path.IsPathFullyQualified(DefenderScanner.BuildStatusScript().Contains(TrustedProcess.DefenderModule.Replace("'", "''")) ? TrustedProcess.PowerShellPath : ""), "status query must use an absolute trusted module and executable");
    Assert(DefenderScanner.BuildStatusScript().Contains("$PSModuleAutoLoadingPreference='None'"), "module auto-loading must be disabled");
    var start = new ProcessStartInfo(TrustedProcess.PowerShellPath);
    foreach (var key in new[] { "DOTNET_STARTUP_HOOKS", "COMPlus_ReadyToRun", "CORECLR_PROFILER_PATH", "COR_PROFILER", "PSModulePath" }) start.Environment[key] = "untrusted";
    TrustedProcess.Harden(start);
    Assert(start.WorkingDirectory == TrustedProcess.SystemDirectory, "child working directory must be trusted");
    Assert(!start.Environment.Values.Contains("untrusted"), "injection variables must be removed");
    Assert(start.Environment["CORECLR_ENABLE_PROFILING"] == "0", "profiling must be disabled");
}

void TestBatchLimits()
{
    using var fixture = Fixture.Create();
    var request = fixture.Request();
    AssertThrows<SecurityException>(() => Protocol.ValidateBatch(new ScanBatch([]), Environment.ProcessId));
    AssertThrows<SecurityException>(() => Protocol.ValidateBatch(new ScanBatch([request, request]), Environment.ProcessId));
    AssertThrows<SecurityException>(() => Protocol.ValidateBatch(new ScanBatch(Enumerable.Range(0, 201).Select(i => request with { RequestId = i.ToString() }).ToArray()), Environment.ProcessId));
    Protocol.ValidateBatch(new ScanBatch([request, request with { RequestId = "second" }]), Environment.ProcessId);
}

async Task TestWrongServerPid()
{
    var name = $"\\\\.\\pipe\\NightOps.Scanner.{Guid.NewGuid():N}";
    await using var server = SecurePipe.Create(name);
    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var accepted = server.WaitForConnectionAsync(timeout.Token);
    await using (var client = new NamedPipeClientStream(".", name[9..], PipeDirection.InOut, PipeOptions.Asynchronous))
    {
        await client.ConnectAsync(timeout.Token);
        await accepted;
        var rejected = false;
        try { await SecurePipe.WriteAuthenticatedAsync(client, Environment.ProcessId + 100000, "SECRET_NONCE_MUST_NOT_LEAVE_CLIENT", timeout.Token); }
        catch (SecurityException) { rejected = true; }
        Assert(rejected, "wrong server PID must be rejected by Windows before write");
    }
    var bytes = new byte[128];
    Assert(await server.ReadAsync(bytes, timeout.Token) == 0, "wrong PID server must receive zero request bytes");
}

void TestCleanRequiresStatus()
{
    var request = new ScanRequest("r", new string('n', 32), "m", "C:\\x", new string('a', 64), 1, Environment.ProcessId);
    var incomplete = new DefenderOutcome(0, "", "", null);
    var result = Protocol.CreateResult(request, Environment.ProcessId, incomplete, 1.0);
    Assert(!result.Ok && result.ReasonCode == "defender_status_unavailable", "zero exit without status must fail closed");
}

void TestDisabledDefender()
{
    var request = new ScanRequest("r", new string('n', 32), new string('a', 32), "C:\\x", new string('a', 64), 1, Environment.ProcessId);
    foreach (var status in new[] {
        new DefenderStatus("1", "2", "3", false, true, "Normal"),
        new DefenderStatus("1", "2", "3", true, false, "Normal"),
        new DefenderStatus("1", "2", "3", true, true, "Passive"),
    })
    {
        var result = Protocol.CreateResult(request, Environment.ProcessId, new DefenderOutcome(0, "", "", status), 1.0);
        Assert(!result.Ok && result.ReasonCode == "defender_status_unavailable", "disabled/passive Defender must fail closed");
    }
}

async Task TestOneShotHelper()
{
    using var fixture = Fixture.Create();
    var helper = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(typeof(Protocol).Assembly.Location)!, "RedLotus.ScanHelper.exe");
    Assert(File.Exists(helper), "helper apphost is missing");
    var fullPipe = $"\\\\.\\pipe\\NightOps.Scanner.{Guid.NewGuid():N}";
    var start = new ProcessStartInfo(helper) { UseShellExecute = false, CreateNoWindow = true };
    foreach (var argument in new[] { "--pipe", fullPipe, "--cache-root", fixture.Root, "--expected-client-pid", Environment.ProcessId.ToString() })
        start.ArgumentList.Add(argument);
    using var process = Process.Start(start) ?? throw new Exception("could not start helper");
    var original = fixture.Request();
    var request = original with { ExpectedHelperPid = process.Id };
    await using var client = new NamedPipeClientStream(".", fullPipe[9..], PipeDirection.InOut, PipeOptions.Asynchronous);
    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
    await client.ConnectAsync(timeout.Token);
    var batch = new ScanBatch([request, request with { RequestId = "second" }]);
    await SecurePipe.WriteAuthenticatedAsync(client, process.Id, JsonSerializer.Serialize(batch), timeout.Token);
    using var reader = new StreamReader(client, Encoding.UTF8, false, 4096, leaveOpen: true);
    var line = await reader.ReadLineAsync(timeout.Token) ?? throw new Exception("helper returned no result");
    var results = JsonSerializer.Deserialize<ScanBatchResponse>(line) ?? throw new Exception("helper result was malformed");
    Assert(results.Results.Length == 2, "one helper process must return both batch file results");
    var result = results.Results[0];
    Assert(result.RequestId == request.RequestId && result.Nonce == request.Nonce, "result identity mismatch");
    Assert(result.HelperPid == process.Id && result.Sha256 == request.Sha256 && result.SizeBytes == request.SizeBytes, "result process/content mismatch");
    Assert(result.Ok ? result.State == "CLEAN" && result.DefenderState == "clean" && result.ExitCode == 0 : result.State != "CLEAN", "result did not fail closed");
    await process.WaitForExitAsync(timeout.Token);
}

void Assert(bool condition, string message)
{
    if (!condition) throw new Exception(message);
}

void AssertThrows<T>(Action action) where T : Exception
{
    try { action(); }
    catch (T) { return; }
    throw new Exception($"expected {typeof(T).Name}");
}

sealed class Fixture : IDisposable
{
    public required string Root { get; init; }
    public required string Path { get; init; }
    public static Fixture Create()
    {
        var root = System.IO.Path.Combine(AppContext.BaseDirectory, "fixtures", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var path = System.IO.Path.Combine(root, "source.mp3");
        File.WriteAllBytes(path, [0xff, 0xfb, 0x90, 0, 1, 2, 3]);
        return new Fixture { Root = root, Path = path };
    }
    public ScanRequest Request()
    {
        var bytes = File.ReadAllBytes(Path);
        return new ScanRequest("request", new string('n', 32), new string('a', 32), Path,
            Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant(), bytes.Length, Environment.ProcessId);
    }
    public void Dispose() => Directory.Delete(Root, true);
}
