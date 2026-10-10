using Microsoft.Win32.SafeHandles;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

namespace NightOps.ScannerHelper;

public sealed class ManagedFile : IDisposable
{
    private readonly FileStream stream;
    public string Path { get; }
    public string Sha256 { get; private set; }
    public long SizeBytes { get; private set; }

    private ManagedFile(string path, FileStream stream)
    {
        Path = path;
        this.stream = stream;
        (Sha256, SizeBytes) = HashOpenStream(stream);
    }

    public static ManagedFile OpenValidated(string candidate, string managedRoot)
    {
        var root = System.IO.Path.GetFullPath(managedRoot).TrimEnd(System.IO.Path.DirectorySeparatorChar);
        var full = System.IO.Path.GetFullPath(candidate);
        EnsureBeneath(full, root);
        RejectReparsePoints(full, root);
        var stream = new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024,
            FileOptions.SequentialScan);
        try
        {
            var finalPath = FinalPath(stream.SafeFileHandle);
            EnsureBeneath(finalPath, root);
            return new ManagedFile(finalPath, stream);
        }
        catch { stream.Dispose(); throw; }
    }

    public bool Matches(string sha256, long sizeBytes)
        => SizeBytes == sizeBytes && CryptographicOperations.FixedTimeEquals(
            Convert.FromHexString(Sha256), Convert.FromHexString(sha256));

    public bool RehashMatches(string sha256, long sizeBytes)
    {
        (Sha256, SizeBytes) = HashOpenStream(stream);
        return Matches(sha256, sizeBytes);
    }

    private static (string Hash, long Size) HashOpenStream(FileStream stream)
    {
        stream.Position = 0;
        using var hash = SHA256.Create();
        var bytes = hash.ComputeHash(stream);
        var size = stream.Length;
        stream.Position = 0;
        return (Convert.ToHexString(bytes).ToLowerInvariant(), size);
    }

    private static void EnsureBeneath(string candidate, string root)
    {
        var prefix = root + System.IO.Path.DirectorySeparatorChar;
        if (!candidate.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            throw new SecurityException("media path is outside managed storage");
    }

    private static void RejectReparsePoints(string candidate, string root)
    {
        var current = new DirectoryInfo(root);
        if ((current.Attributes & FileAttributes.ReparsePoint) != 0)
            throw new SecurityException("managed root is a reparse point");
        var relative = System.IO.Path.GetRelativePath(root, candidate);
        foreach (var segment in relative.Split(System.IO.Path.DirectorySeparatorChar))
        {
            current = new DirectoryInfo(System.IO.Path.Combine(current.FullName, segment));
            var attributes = File.GetAttributes(current.FullName);
            if ((attributes & FileAttributes.ReparsePoint) != 0)
                throw new SecurityException("media path contains a reparse point");
        }
    }

    private static string FinalPath(SafeFileHandle handle)
    {
        var buffer = new char[32768];
        var length = GetFinalPathNameByHandle(handle, buffer, (uint)buffer.Length, 0);
        if (length == 0 || length >= buffer.Length)
            throw new SecurityException("could not resolve opened media file");
        var value = new string(buffer, 0, (int)length);
        return value.StartsWith("\\\\?\\", StringComparison.Ordinal) ? value[4..] : value;
    }

    public void Dispose() => stream.Dispose();

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(SafeFileHandle hFile, [Out] char[] lpszFilePath,
        uint cchFilePath, uint dwFlags);
}
