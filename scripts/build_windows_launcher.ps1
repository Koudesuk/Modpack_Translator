param(
    [string]$OutputPath = "",
    [switch]$SkipIcon
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$VersionFile = Join-Path $ProjectRoot "src\modpack_translator\version.py"
$VersionText = Get-Content $VersionFile -Raw
if ($VersionText -notmatch '__version__\s*=\s*"([^"]+)"') {
    throw "Cannot read app version from $VersionFile"
}
$AppVersion = $Matches[1]
$LauncherBaseName = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("5qih57WE5YyF57+76K2v5Zmo"))
$UsingDefaultOutput = -not $OutputPath
if (-not $OutputPath) {
    $OutputPath = Join-Path $ProjectRoot "$($LauncherBaseName)v$AppVersion.exe"
}

$PngIconPath = Join-Path $ProjectRoot "assets\icon\app_icon.png"
$IcoIconPath = Join-Path $ProjectRoot "assets\icon\app_icon.ico"

function Convert-PngToIco {
    param(
        [Parameter(Mandatory = $true)][string]$PngPath,
        [Parameter(Mandatory = $true)][string]$IcoPath
    )

    Add-Type -AssemblyName System.Drawing

    $source = [System.Drawing.Image]::FromFile($PngPath)
    $bitmap = New-Object System.Drawing.Bitmap 256, 256
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $memory = New-Object System.IO.MemoryStream
    $file = $null
    $writer = $null

    try {
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphics.DrawImage($source, 0, 0, 256, 256)
        $bitmap.Save($memory, [System.Drawing.Imaging.ImageFormat]::Png)
        $pngBytes = $memory.ToArray()

        $file = [System.IO.File]::Create($IcoPath)
        $writer = New-Object System.IO.BinaryWriter($file)
        $writer.Write([UInt16]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]1)
        $writer.Write([Byte]0)
        $writer.Write([Byte]0)
        $writer.Write([Byte]0)
        $writer.Write([Byte]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]32)
        $writer.Write([UInt32]$pngBytes.Length)
        $writer.Write([UInt32]22)
        $writer.Write($pngBytes)
    }
    finally {
        if ($writer) { $writer.Dispose() }
        if ($file) { $file.Dispose() }
        $memory.Dispose()
        $graphics.Dispose()
        $bitmap.Dispose()
        $source.Dispose()
    }
}

if (-not $SkipIcon -and (Test-Path -LiteralPath $PngIconPath) -and -not (Test-Path -LiteralPath $IcoIconPath)) {
    Convert-PngToIco -PngPath $PngIconPath -IcoPath $IcoIconPath
}

$source = @"
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

// 版本資源。沒有中繼資料的未簽章小型執行檔，是防毒 ML 模型最愛的特徵之一。
[assembly: AssemblyTitle("Minecraft Modpack Translator Launcher")]
[assembly: AssemblyDescription("Starts the Minecraft modpack translator application.")]
[assembly: AssemblyProduct("Minecraft Modpack Translator")]
[assembly: AssemblyCompany("Koudesuk")]
[assembly: AssemblyCopyright("MIT License")]
[assembly: AssemblyVersion("$AppVersion.0")]
[assembly: AssemblyFileVersion("$AppVersion.0")]

internal static class Program
{
    [STAThread]
    private static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string runtime = Path.Combine(root, ".runtime");
        Directory.CreateDirectory(runtime);

        string uv = FindOnPath("uv.exe");
        if (uv == null)
        {
            MessageBox.Show(
                "uv was not found. Install uv, then run setup_windows.bat before starting the app.",
                "Minecraft Modpack Translator",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 1;
        }

        if (!File.Exists(Path.Combine(root, "setup_windows.bat")))
        {
            MessageBox.Show(
                "setup_windows.bat was not found. This folder is not a complete app package.",
                "Minecraft Modpack Translator",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 1;
        }

        if (!Directory.Exists(Path.Combine(root, ".venv")) || !File.Exists(Path.Combine(runtime, "backend.json")))
        {
            MessageBox.Show(
                "First-time setup is required. Please run setup_windows.bat once, then open this launcher again.",
                "Minecraft Modpack Translator",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information
            );
            return 1;
        }

        // 直接啟動 uv 並用 .NET 接管輸出。
        // 舊版是 cmd.exe /c "… > log 2>&1" 且隱藏視窗——未簽章的小程式靜默拉起
        // 命令列殼層再重導輸出，正是防毒啟發式判定 dropper 的典型特徵，v1.4.1 因此
        // 被 Defender 標成 Trojan:Win32/Suschil!rfn。不經殼層就沒有這個特徵。
        string logPath = Path.Combine(runtime, "launcher.log");

        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = uv;
        info.Arguments = "run python main.py";
        info.WorkingDirectory = root;
        info.CreateNoWindow = true;
        info.UseShellExecute = false;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;

        Process process = Process.Start(info);
        StreamWriter log = new StreamWriter(logPath, false);
        log.AutoFlush = true;
        process.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
        {
            if (e.Data != null) { lock (log) { log.WriteLine(e.Data); } }
        };
        process.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
        {
            if (e.Data != null) { lock (log) { log.WriteLine(e.Data); } }
        };
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        process.WaitForExit();
        log.Close();
        return process.ExitCode;
    }

    // 自己走 PATH 找 uv.exe，取代 cmd.exe /c where uv。
    private static string FindOnPath(string fileName)
    {
        string pathVar = Environment.GetEnvironmentVariable("PATH");
        if (pathVar == null) { return null; }
        foreach (string dir in pathVar.Split(Path.PathSeparator))
        {
            if (dir.Length == 0) { continue; }
            string candidate;
            try { candidate = Path.Combine(dir.Trim('"'), fileName); }
            catch (ArgumentException) { continue; }
            if (File.Exists(candidate)) { return candidate; }
        }
        return null;
    }
}
"@

$compilerOptions = "/target:winexe /platform:anycpu"
if (-not $SkipIcon -and (Test-Path -LiteralPath $IcoIconPath)) {
    $compilerOptions += " /win32icon:`"$IcoIconPath`""
}

Add-Type -AssemblyName Microsoft.CSharp
$provider = New-Object Microsoft.CSharp.CSharpCodeProvider
$parameters = New-Object System.CodeDom.Compiler.CompilerParameters
$parameters.GenerateExecutable = $true
$parameters.GenerateInMemory = $false
$parameters.OutputAssembly = $OutputPath
$parameters.CompilerOptions = $compilerOptions
[void]$parameters.ReferencedAssemblies.Add("System.dll")
[void]$parameters.ReferencedAssemblies.Add("System.Windows.Forms.dll")
[void]$parameters.ReferencedAssemblies.Add("System.Drawing.dll")

$result = $provider.CompileAssemblyFromSource($parameters, $source)
if ($result.Errors.HasErrors) {
    $messages = @()
    foreach ($err in $result.Errors) {
        $messages += "$($err.FileName):$($err.Line):$($err.Column): $($err.ErrorText)"
    }
    throw "Launcher compile failed:`n$($messages -join "`n")"
}

if ($UsingDefaultOutput) {
    Get-ChildItem -LiteralPath $ProjectRoot -Filter "$($LauncherBaseName)v*.exe" |
        Where-Object { $_.FullName -ne (Resolve-Path $OutputPath).Path } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

Write-Host "Windows launcher built: $OutputPath"
