using System.Diagnostics;
using System.IO.Pipes;
using System.Text.Json;

namespace NightOps.ScannerHelper;

public static class NativeExchange
{
    public const int MaximumMessageBytes = 2 * 1024 * 1024;

    public static async Task<int> RunAsync(string[] args)
    {
        if (args.Length != 5 || args[0] != "--exchange" || args[1] != "--pipe" || args[3] != "--cache-root")
            throw new ArgumentException("invalid exchange arguments");
        var pipeName = args[2];
        if (!pipeName.StartsWith("\\\\.\\pipe\\NightOps.Scanner.", StringComparison.Ordinal) || pipeName.Length > 240)
            throw new SecurityException("invalid exchange pipe name");
        var root = Path.GetFullPath(args[4]);
        TrustedProcess.SanitizeCurrentEnvironment();
        Environment.CurrentDirectory = TrustedProcess.SystemDirectory;
        var executable = Environment.ProcessPath ?? throw new SecurityException("helper executable path unavailable");
        var start = new ProcessStartInfo(executable) {
            UseShellExecute = true, Verb = "runas", WindowStyle = ProcessWindowStyle.Hidden,
            WorkingDirectory = TrustedProcess.SystemDirectory,
        };
        foreach (var argument in new[] { "--pipe", pipeName, "--cache-root", root, "--expected-client-pid", Environment.ProcessId.ToString() })
            start.ArgumentList.Add(argument);
        // Retain this process handle throughout the exchange. The claimed PID
        // is from the OS launch, never from JSON supplied by a pipe server.
        using var helper = Process.Start(start) ?? throw new IOException("elevated helper did not start");
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(30));
        await using var client = new NamedPipeClientStream(".", pipeName[9..], PipeDirection.InOut, PipeOptions.Asynchronous);
        await client.ConnectAsync(timeout.Token);
        if (helper.HasExited) throw new SecurityException("elevated helper exited before exchange");
        SecurePipe.VerifyServerProcess(client, helper.Id);
        Console.WriteLine(JsonSerializer.Serialize(new { helper_pid = helper.Id }));
        await Console.Out.FlushAsync();
        var payload = await SecurePipe.ReadLineAsync(Console.OpenStandardInput(), MaximumMessageBytes, timeout.Token);
        if (helper.HasExited) throw new SecurityException("elevated helper exited before request delivery");
        await SecurePipe.WriteAuthenticatedAsync(client, helper.Id, payload, timeout.Token);
        var response = await SecurePipe.ReadLineAsync(client, MaximumMessageBytes, timeout.Token);
        Console.WriteLine(response);
        await Console.Out.FlushAsync();
        await helper.WaitForExitAsync(timeout.Token);
        return 0;
    }
}
