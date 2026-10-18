using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace WalkmanSmoke
{
    // Identity and termination use the same kernel object. Keeping this handle
    // open prevents a later numeric-PID lookup from selecting a replacement.
    public sealed class ProcessHandle : SafeHandleZeroOrMinusOneIsInvalid
    {
        private ProcessHandle() : base(true) { }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern ProcessHandle OpenProcess(uint access, bool inherit, int processId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetProcessTimes(ProcessHandle process, out long created,
            out long exited, out long kernel, out long user);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateProcess(ProcessHandle process, uint exitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);

        public static ProcessHandle Open(int processId)
        {
            const uint TerminateAndQuery = 0x0001 | 0x1000;
            ProcessHandle process = OpenProcess(TerminateAndQuery, false, processId);
            if (process.IsInvalid)
            {
                int error = Marshal.GetLastWin32Error();
                process.Dispose();
                throw new Win32Exception(error);
            }
            return process;
        }

        public DateTime CreationTimeUtc
        {
            get
            {
                long created, exited, kernel, user;
                if (!GetProcessTimes(this, out created, out exited, out kernel, out user))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                return DateTime.FromFileTimeUtc(created);
            }
        }

        public void Terminate()
        {
            if (!TerminateProcess(this, 1))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }

        protected override bool ReleaseHandle() { return CloseHandle(handle); }
    }
}
