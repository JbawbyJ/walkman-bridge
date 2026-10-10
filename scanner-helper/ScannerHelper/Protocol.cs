using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace NightOps.ScannerHelper;

public sealed record ScanBatch([property: JsonPropertyName("requests")] ScanRequest[] Requests);
public sealed record ScanBatchResponse([property: JsonPropertyName("results")] ScanResponse[] Results);

public sealed record ScanRequest(
    [property: JsonPropertyName("request_id")] string RequestId,
    [property: JsonPropertyName("nonce")] string Nonce,
    [property: JsonPropertyName("media_id")] string MediaId,
    [property: JsonPropertyName("path")] string Path,
    [property: JsonPropertyName("sha256")] string Sha256,
    [property: JsonPropertyName("size_bytes")] long SizeBytes,
    [property: JsonPropertyName("expected_helper_pid")] int ExpectedHelperPid);

public sealed record DefenderStatus(
    [property: JsonPropertyName("engine_version")] string EngineVersion,
    [property: JsonPropertyName("signature_version")] string SignatureVersion,
    [property: JsonPropertyName("platform_version")] string PlatformVersion,
    [property: JsonPropertyName("active")] bool Active,
    [property: JsonPropertyName("real_time_protection")] bool RealTimeProtection,
    [property: JsonPropertyName("running_mode")] string RunningMode)
{
    public bool Complete => Active && RealTimeProtection
        && string.Equals(RunningMode, "Normal", StringComparison.OrdinalIgnoreCase)
        && !string.IsNullOrWhiteSpace(EngineVersion)
        && !string.IsNullOrWhiteSpace(SignatureVersion)
        && !string.IsNullOrWhiteSpace(PlatformVersion);
}

public sealed record DefenderOutcome(int ExitCode, string Stdout, string Stderr, DefenderStatus? Status);

public sealed record ScanResponse(
    [property: JsonPropertyName("request_id")] string RequestId,
    [property: JsonPropertyName("nonce")] string Nonce,
    [property: JsonPropertyName("media_id")] string MediaId,
    [property: JsonPropertyName("path")] string Path,
    [property: JsonPropertyName("sha256")] string Sha256,
    [property: JsonPropertyName("size_bytes")] long SizeBytes,
    [property: JsonPropertyName("helper_pid")] int HelperPid,
    [property: JsonPropertyName("ok")] bool Ok,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("reason")] string Reason,
    [property: JsonPropertyName("reason_code")] string ReasonCode,
    [property: JsonPropertyName("defender_status")] string DefenderState,
    [property: JsonPropertyName("defender_engine_version")] string EngineVersion,
    [property: JsonPropertyName("defender_signature_version")] string SignatureVersion,
    [property: JsonPropertyName("defender_platform_version")] string PlatformVersion,
    [property: JsonPropertyName("exit_code")] int ExitCode,
    [property: JsonPropertyName("scanned_at")] double ScannedAt);

public static partial class Protocol
{
    public const int MaximumBatchFiles = 200;

    public static void ValidateBatch(ScanBatch batch, int helperPid)
    {
        if (batch.Requests is null || batch.Requests.Length < 1 || batch.Requests.Length > MaximumBatchFiles)
            throw new SecurityException("invalid scanner batch size");
        if (batch.Requests.Select(request => request.RequestId).Distinct(StringComparer.Ordinal).Count() != batch.Requests.Length)
            throw new SecurityException("duplicate scanner request identity");
        foreach (var request in batch.Requests) ValidateRequest(request, helperPid);
    }
    [GeneratedRegex("^[0-9a-f]{32}$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex MediaIdPattern();
    [GeneratedRegex("^[0-9a-f]{64}$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex HashPattern();
    [GeneratedRegex("access\\s+is\\s+denied|access\\s+denied|0x80070005", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex AccessDeniedPattern();
    [GeneratedRegex("\\bthreat(?:s)?\\b.*\\b(?:found|detected)\\b|\\bfound\\s+[1-9]\\d*\\s+threat", RegexOptions.IgnoreCase | RegexOptions.Singleline | RegexOptions.CultureInvariant)]
    private static partial Regex ThreatPattern();

    public static void ValidateRequest(ScanRequest request, int helperPid)
    {
        if (string.IsNullOrWhiteSpace(request.RequestId) || request.RequestId.Length > 128)
            throw new SecurityException("invalid request id");
        if (request.Nonce.Length < 32 || request.Nonce.Length > 256)
            throw new SecurityException("invalid request nonce");
        if (!MediaIdPattern().IsMatch(request.MediaId))
            throw new SecurityException("invalid media id");
        if (request.Path.Length > 4096 || !System.IO.Path.IsPathFullyQualified(request.Path))
            throw new SecurityException("media path is not absolute");
        if (!HashPattern().IsMatch(request.Sha256) || request.SizeBytes < 0)
            throw new SecurityException("invalid content identity");
        if (request.ExpectedHelperPid != helperPid)
            throw new SecurityException("request was addressed to another helper process");
    }

    public static ScanResponse CreateResult(ScanRequest request, int helperPid, DefenderOutcome outcome, double scannedAt)
    {
        var text = $"{outcome.Stdout}\n{outcome.Stderr}";
        var status = outcome.Status;
        bool ok;
        string state;
        string reason;
        string code;
        string defenderState;
        if (outcome.ExitCode == 2 || ThreatPattern().IsMatch(text))
        {
            ok = false; state = "BLOCKED"; reason = "Microsoft Defender reported a threat";
            code = "defender_threat"; defenderState = "threat";
        }
        else if (outcome.ExitCode != 0 || AccessDeniedPattern().IsMatch(text))
        {
            ok = false; state = "ERROR"; reason = $"Microsoft Defender elevated scan failed with code {outcome.ExitCode}";
            code = "defender_scan_error"; defenderState = "error";
        }
        else if (status is null || !status.Complete)
        {
            ok = false; state = "ERROR"; reason = "Microsoft Defender signature status is unavailable";
            code = "defender_status_unavailable"; defenderState = "unavailable";
        }
        else
        {
            ok = true; state = "CLEAN"; reason = "Microsoft Defender reported clean";
            code = "clean"; defenderState = "clean";
        }
        return new ScanResponse(
            request.RequestId, request.Nonce, request.MediaId, request.Path, request.Sha256,
            request.SizeBytes, helperPid, ok, state, reason, code, defenderState,
            status?.EngineVersion ?? "", status?.SignatureVersion ?? "",
            status?.PlatformVersion ?? "", outcome.ExitCode, scannedAt);
    }
}
