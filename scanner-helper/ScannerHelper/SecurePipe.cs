using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;

namespace NightOps.ScannerHelper;

public static class SecurePipe
{
    public static NamedPipeServerStream Create(string fullPipeName)
    {
        const string prefix = "\\\\.\\pipe\\NightOps.Scanner.";
        if (!fullPipeName.StartsWith(prefix, StringComparison.Ordinal) || fullPipeName.Length > 240)
            throw new SecurityException("invalid scanner pipe name");
        var pipeName = fullPipeName[9..];
        var user = WindowsIdentity.GetCurrent().User ?? throw new SecurityException("current Windows user has no SID");
        var administrators = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
        var security = new PipeSecurity();
        security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        security.AddAccessRule(new PipeAccessRule(user, PipeAccessRights.ReadWrite, AccessControlType.Allow));
        security.AddAccessRule(new PipeAccessRule(administrators, PipeAccessRights.FullControl, AccessControlType.Allow));
        return NamedPipeServerStreamAcl.Create(pipeName, PipeDirection.InOut, 1,
            PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 64 * 1024, 64 * 1024, security);
    }

    public static void VerifyClientProcess(NamedPipeServerStream pipe, int expectedPid)
    {
        if (!GetNamedPipeClientProcessId(pipe.SafePipeHandle.DangerousGetHandle(), out var actual)
            || actual != (uint)expectedPid)
            throw new SecurityException("unexpected scanner pipe client process");
    }

    public static void VerifyServerProcess(NamedPipeClientStream pipe, int expectedPid)
    {
        if (expectedPid <= 0 || !GetNamedPipeServerProcessId(pipe.SafePipeHandle.DangerousGetHandle(), out var actual)
            || actual != (uint)expectedPid)
            throw new SecurityException("unexpected scanner pipe server process");
    }

    public static async Task WriteAuthenticatedAsync(NamedPipeClientStream pipe, int expectedPid,
        string payload, CancellationToken cancellationToken)
    {
        // No nonce or content identity is written until the kernel identifies
        // the server. Callers retain the launched helper's process handle.
        VerifyServerProcess(pipe, expectedPid);
        await pipe.WriteAsync(Encoding.UTF8.GetBytes(payload + "\n"), cancellationToken);
        await pipe.FlushAsync(cancellationToken);
    }

    public static async Task<string> ReadLineAsync(Stream stream, int maxBytes, CancellationToken cancellationToken)
    {
        using var memory = new MemoryStream();
        var one = new byte[1];
        while (memory.Length <= maxBytes)
        {
            var read = await stream.ReadAsync(one, cancellationToken);
            if (read == 0) throw new IOException("scanner broker disconnected before sending a request");
            if (one[0] == (byte)'\n') return Encoding.UTF8.GetString(memory.ToArray());
            memory.WriteByte(one[0]);
        }
        throw new IOException("scanner request exceeded the protocol limit");
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetNamedPipeClientProcessId(IntPtr pipe, out uint clientProcessId);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetNamedPipeServerProcessId(IntPtr pipe, out uint serverProcessId);
}
