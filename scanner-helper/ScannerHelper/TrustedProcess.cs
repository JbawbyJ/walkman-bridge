using System.Collections;
using System.Diagnostics;

namespace NightOps.ScannerHelper;

public static class TrustedProcess
{
    public static bool IsInjectionVariable(string name) =>
        new[] { "DOTNET_", "COMPLUS_", "CORECLR_", "COR_" }.Any(prefix => name.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        || name.Equals("PSModulePath", StringComparison.OrdinalIgnoreCase);

    public static string SystemDirectory => Environment.GetFolderPath(Environment.SpecialFolder.System);
    public static string PowerShellPath => Path.Combine(SystemDirectory, "WindowsPowerShell", "v1.0", "powershell.exe");
    public static string DefenderModule => Path.Combine(SystemDirectory, "WindowsPowerShell", "v1.0", "Modules", "Defender", "Defender.psd1");

    public static void SanitizeCurrentEnvironment()
    {
        foreach (DictionaryEntry pair in Environment.GetEnvironmentVariables())
            if (IsInjectionVariable((string)pair.Key)) Environment.SetEnvironmentVariable((string)pair.Key, null);
        Environment.SetEnvironmentVariable("DOTNET_EnableDiagnostics", "0");
        Environment.SetEnvironmentVariable("DOTNET_EnableDiagnostics_IPC", "0");
        Environment.SetEnvironmentVariable("DOTNET_EnableDiagnostics_Debugger", "0");
        Environment.SetEnvironmentVariable("DOTNET_EnableDiagnostics_Profiler", "0");
        Environment.SetEnvironmentVariable("CORECLR_ENABLE_PROFILING", "0");
        Environment.SetEnvironmentVariable("COR_ENABLE_PROFILING", "0");
    }

    public static void Harden(ProcessStartInfo start)
    {
        start.WorkingDirectory = SystemDirectory;
        foreach (var name in start.Environment.Keys.ToArray())
            if (IsInjectionVariable(name)) start.Environment.Remove(name);
        start.Environment["PATH"] = SystemDirectory;
        start.Environment["PSModulePath"] = Path.Combine(SystemDirectory, "WindowsPowerShell", "v1.0", "Modules");
        start.Environment["DOTNET_EnableDiagnostics"] = "0";
        start.Environment["CORECLR_ENABLE_PROFILING"] = "0";
        start.Environment["COR_ENABLE_PROFILING"] = "0";
    }
}
