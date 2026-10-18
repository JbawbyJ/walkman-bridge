using System.Diagnostics;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace NightOps.ScannerHelper;

public static class DefenderScanner
{
    public static string[] BuildScanArguments(string path)
        => ["-Scan", "-ScanType", "3", "-File", path, "-DisableRemediation"];

    public static async Task<DefenderOutcome> ScanAsync(string path, CancellationToken cancellationToken)
    {
        var executable = LocateDefender() ?? throw new FileNotFoundException("Microsoft Defender command line scanner is unavailable");
        using var scanner = OpenTrustedScanner(executable);
        var result = await RunAsync(executable, BuildScanArguments(path), cancellationToken);
        var status = result.ExitCode == 0 ? await QueryStatusAsync(cancellationToken) : null;
        return new DefenderOutcome(result.ExitCode, result.Stdout, result.Stderr, status);
    }

    private static IEnumerable<string> DefenderCandidates()
    {
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        var installed = System.IO.Path.Combine(programFiles, "Windows Defender", "MpCmdRun.exe");
        yield return installed;
        var platform = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
            "Microsoft", "Windows Defender", "Platform");
        if (!Directory.Exists(platform) || HasReparseComponent(platform)) yield break;
        foreach (var folder in Directory.EnumerateDirectories(platform)
            .Where(value => Regex.IsMatch(Path.GetFileName(value), @"^\d+\.\d+\.\d+\.\d+(?:-\d+)?$"))
            .OrderByDescending(value => Version.Parse(Path.GetFileName(value).Split('-')[0])))
            yield return Path.Combine(folder, "MpCmdRun.exe");
    }

    public static string? LocateDefender()
        => DefenderCandidates().FirstOrDefault(path => File.Exists(path) && !HasReparseComponent(path));

    private static bool HasReparseComponent(string path)
    {
        for (var current = path; current != null; current = Path.GetDirectoryName(current))
            if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) return true;
        return false;
    }

    public static ManagedFile OpenTrustedScanner(string executable)
    {
        if (!Path.IsPathFullyQualified(executable)
            || !DefenderCandidates().Contains(executable, StringComparer.OrdinalIgnoreCase)
            || HasReparseComponent(executable))
            throw new SecurityException("untrusted Microsoft Defender executable location");
        // Existing managed-file handle validation also denies write/delete
        // sharing and resolves the final OS path. Retain it through process exit.
        var opened = ManagedFile.OpenValidated(executable, Path.GetDirectoryName(executable)!);
        if (!opened.Path.Equals(executable, StringComparison.OrdinalIgnoreCase))
        {
            opened.Dispose();
            throw new SecurityException("Microsoft Defender executable identity changed");
        }
        return opened;
    }

    public static string BuildStatusScript()
    {
        var module = TrustedProcess.DefenderModule.Replace("'", "''");
        var utility = Path.Combine(TrustedProcess.SystemDirectory, "WindowsPowerShell", "v1.0", "Modules", "Microsoft.PowerShell.Utility", "Microsoft.PowerShell.Utility.psd1").Replace("'", "''");
        return "$PSModuleAutoLoadingPreference='None';Import-Module -Name '" + utility + "' -ErrorAction Stop;Import-Module -Name '" + module + "' -ErrorAction Stop;"
            + "$s=Defender\\Get-MpComputerStatus -ErrorAction Stop;"
            + "@{engine_version=$s.AMEngineVersion;signature_version=$s.AntivirusSignatureVersion;platform_version=$s.AMProductVersion;active=$s.AntivirusEnabled;real_time_protection=$s.RealTimeProtectionEnabled;running_mode=$s.AMRunningMode}|Microsoft.PowerShell.Utility\\ConvertTo-Json -Compress";
    }

    public static async Task<DefenderStatus?> QueryStatusAsync(CancellationToken cancellationToken)
    {
        try
        {
            var result = await RunAsync(TrustedProcess.PowerShellPath, ["-NoProfile", "-NonInteractive", "-Command", BuildStatusScript()], cancellationToken);
            if (result.ExitCode != 0) return null;
            return JsonSerializer.Deserialize<DefenderStatus>(result.Stdout);
        }
        catch (Exception) when (!cancellationToken.IsCancellationRequested) { return null; }
    }

    private static async Task<(int ExitCode, string Stdout, string Stderr)> RunAsync(
        string executable, IEnumerable<string> arguments, CancellationToken cancellationToken)
    {
        var start = new ProcessStartInfo(executable) {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
        };
        TrustedProcess.Harden(start);
        foreach (var argument in arguments) start.ArgumentList.Add(argument);
        using var process = new Process { StartInfo = start };
        if (!process.Start()) throw new InvalidOperationException("could not start Microsoft Defender scanner");
        var stdout = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderr = process.StandardError.ReadToEndAsync(cancellationToken);
        try { await process.WaitForExitAsync(cancellationToken); }
        catch (OperationCanceledException) { try { process.Kill(true); } catch { } throw; }
        return (process.ExitCode, await stdout, await stderr);
    }
}
