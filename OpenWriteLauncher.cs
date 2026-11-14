using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace OpenWriteLauncher
{
    static class Program
    {
        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        private const int SW_RESTORE = 9;

        [STAThread]
        static void Main(string[] args)
        {
            // Mutex to ensure single instance
            bool isNewInstance;
            using (Mutex mutex = new Mutex(true, "OpenWrite_Native_Voice_Assistant_Mutex", out isNewInstance))
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string scriptPath = Path.Combine(baseDir, "desktop_app.py");

                if (!File.Exists(scriptPath))
                {
                    MessageBox.Show("OpenWrite core script not found:\n" + scriptPath, "OpenWrite Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }

                // Check if already running via PID file; cleanly restart old instance
                string pidFile = Path.Combine(baseDir, "data", "desktop_app.pid");
                if (File.Exists(pidFile))
                {
                    try
                    {
                        string pidText = File.ReadAllText(pidFile).Trim();
                        int pid;
                        if (int.TryParse(pidText, out pid))
                        {
                            try
                            {
                                Process existingProc = Process.GetProcessById(pid);
                                if (existingProc != null && !existingProc.HasExited)
                                {
                                    try
                                    {
                                        existingProc.Kill();
                                        existingProc.WaitForExit(1000);
                                    }
                                    catch { }
                                }
                            }
                            catch { }
                        }
                    }
                    catch { }
                }

                // Locate pythonw.exe
                string pythonw = FindPythonW();
                if (string.IsNullOrEmpty(pythonw) || !File.Exists(pythonw))
                {
                    MessageBox.Show("Python (pythonw.exe) was not found.\nPlease ensure Python 3.10+ is installed.", "OpenWrite Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }

                // Launch pythonw.exe desktop_app.py as an independent Windows shell process
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = pythonw;
                psi.Arguments = "\"" + scriptPath + "\"";
                psi.WorkingDirectory = baseDir;
                psi.UseShellExecute = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;

                try
                {
                    Process.Start(psi);
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Failed to start OpenWrite:\n" + ex.Message, "OpenWrite Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
        }

        private static string FindPythonW()
        {
            string[] candidates = new string[]
            {
                @"C:\Program Files\Python310\pythonw.exe",
                @"C:\Python310\pythonw.exe",
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python310\pythonw.exe"),
                @"C:\Program Files\Python311\pythonw.exe",
                @"C:\Program Files\Python312\pythonw.exe"
            };

            foreach (string path in candidates)
            {
                if (File.Exists(path)) return path;
            }

            string pathEnv = Environment.GetEnvironmentVariable("PATH");
            if (!string.IsNullOrEmpty(pathEnv))
            {
                foreach (string dir in pathEnv.Split(';'))
                {
                    string trimmed = dir.Trim();
                    if (!string.IsNullOrEmpty(trimmed))
                    {
                        string p1 = Path.Combine(trimmed, "pythonw.exe");
                        if (File.Exists(p1)) return p1;
                        string p2 = Path.Combine(trimmed, "python.exe");
                        if (File.Exists(p2)) return p2;
                    }
                }
            }
            return null;
        }
    }
}
