using System.Text;
using System.Text.Json;
using NightOps.ScannerHelper;

return await MainAsync(args);

static async Task<int> MainAsync(string[] args)
{
    try
    {
        if (args.FirstOrDefault() == "--exchange") return await NativeExchange.RunAsync(args);
        var options = HelperOptions.Parse(args);
        TrustedProcess.SanitizeCurrentEnvironment();
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(30));
        await using var pipe = SecurePipe.Create(options.PipeName);
        await pipe.WaitForConnectionAsync(timeout.Token);
        SecurePipe.VerifyClientProcess(pipe, options.ExpectedClientPid);
        var raw = await SecurePipe.ReadLineAsync(pipe, NativeExchange.MaximumMessageBytes, timeout.Token);
        var batch = JsonSerializer.Deserialize<ScanBatch>(raw)
            ?? throw new SecurityException("scanner batch is empty");
        Protocol.ValidateBatch(batch, Environment.ProcessId);
        var results = new List<ScanResponse>();
        foreach (var request in batch.Requests)
        {
            DefenderOutcome outcome;
            try
            {
                using var media = ManagedFile.OpenValidated(request.Path, options.CacheRoot);
                if (!media.Matches(request.Sha256, request.SizeBytes))
                    throw new SecurityException("media content does not match the elevation request");
                outcome = await DefenderScanner.ScanAsync(media.Path, timeout.Token);
                if (!media.RehashMatches(request.Sha256, request.SizeBytes))
                    throw new SecurityException("media content changed during the Defender scan");
            }
            catch (Exception exception) when (exception is not OperationCanceledException)
            {
                // Preserve outcomes for other admitted files without returning
                // filesystem details or treating any exception as clean.
                outcome = new DefenderOutcome(-1, "", "managed file scan failed", null);
            }
            results.Add(Protocol.CreateResult(request, Environment.ProcessId, outcome,
                DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0));
        }
        var json = JsonSerializer.Serialize(new ScanBatchResponse(results.ToArray())) + "\n";
        await pipe.WriteAsync(Encoding.UTF8.GetBytes(json), timeout.Token);
        await pipe.FlushAsync(timeout.Token);
        return results.All(result => result.Ok) ? 0 : 2;
    }
    catch (System.ComponentModel.Win32Exception exception) when (exception.NativeErrorCode == 1223)
    {
        Console.Error.WriteLine("elevation_cancelled"); return 4;
    }
    catch (OperationCanceledException) { Console.Error.WriteLine("scanner_timeout"); return 3; }
    catch { Console.Error.WriteLine("scanner_exchange_failed"); return 1; }
}

internal sealed record HelperOptions(string PipeName, string CacheRoot, int ExpectedClientPid)
{
    public static HelperOptions Parse(string[] args)
    {
        if (args.Length != 6) throw new ArgumentException("invalid helper arguments");
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        for (var index = 0; index < args.Length; index += 2)
        {
            if (args[index] is not ("--pipe" or "--cache-root" or "--expected-client-pid")
                || !values.TryAdd(args[index], args[index + 1]))
                throw new ArgumentException("invalid helper argument");
        }
        if (!int.TryParse(values["--expected-client-pid"], out var pid) || pid <= 0)
            throw new ArgumentException("invalid expected client process");
        return new HelperOptions(values["--pipe"], System.IO.Path.GetFullPath(values["--cache-root"]), pid);
    }
}
