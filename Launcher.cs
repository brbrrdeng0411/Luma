using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
using System.Runtime.InteropServices;
using System.Reflection;

[assembly: AssemblyTitle("Luma")]
[assembly: AssemblyProduct("Luma")]
[assembly: AssemblyVersion("1.3.8")]
[assembly: AssemblyFileVersion("1.3.8")]
[assembly: AssemblyInformationalVersion("1.3.8")]

class Launcher {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)]
    static extern int SetCurrentProcessExplicitAppUserModelID(string appId);
    [STAThread]
    static void Main() {
        SetCurrentProcessExplicitAppUserModelID("Luma.Desktop.Library");
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "runtime", "pythonw.exe");
        try {
            if (!File.Exists(python)) throw new Exception("runtime 폴더를 찾을 수 없습니다. Luma 폴더 전체를 함께 보관하세요.");
            var start = new ProcessStartInfo(python, "-I \"" + Path.Combine(root, "bootstrap.py") + "\"");
            start.WorkingDirectory = root;
            start.UseShellExecute = true;
            start.CreateNoWindow = true;
            Process.Start(start);
        } catch (Exception e) {
            try { File.WriteAllText(Path.Combine(root, "launch-error.txt"), e.ToString()); } catch { }
            MessageBox.Show(e.Message, "Luma 실행 오류", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
