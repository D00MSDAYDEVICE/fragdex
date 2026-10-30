# Rebuild app\fragdex\downloads\fragdex-scan.exe, the Windows desktop scanner (app\scanner-win\*.cs).
# Uses the C# compiler that ships with .NET Framework 4.x, so nothing needs installing; the
# result runs on any Windows 10/11 PC as is. Rebuild whenever the demo parser changes.
#   powershell -ExecutionPolicy Bypass -File app\tools\build_scanner_exe.ps1
$ErrorActionPreference = "Stop"
$app = Split-Path $PSScriptRoot -Parent
$src = Join-Path $app "scanner-win"
$out = Join-Path $app "fragdex\downloads\fragdex-scan.exe"
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe" }
if (-not (Test-Path $csc)) { throw "csc.exe from .NET Framework 4.x not found" }

$version = (Select-String -Path (Join-Path $app "fragdex\__init__.py") -Pattern '__version__ = "([0-9.]+)"').Matches[0].Groups[1].Value
$work = Join-Path ([IO.Path]::GetTempPath()) "fragdex-scan-build"
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $work | Out-Null
$info = Join-Path $work "Version.cs"
Set-Content -Encoding ASCII -Path $info -Value @"
[assembly: System.Reflection.AssemblyVersion("$version.0")]
[assembly: System.Reflection.AssemblyFileVersion("$version.0")]
[assembly: System.Reflection.AssemblyInformationalVersion("$version")]
"@
$sources = @(Get-ChildItem $src -Filter *.cs | ForEach-Object { $_.FullName }) + $info

function Invoke-Csc([string]$target, [string[]]$extra) {
    & $csc /nologo /target:winexe /optimize+ /codepage:65001 /warn:4 "/out:$target" `
        /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll `
        /r:System.Windows.Forms.dll /r:System.Drawing.dll @extra @sources
    if ($LASTEXITCODE -ne 0) { throw "csc failed ($LASTEXITCODE)" }
}

# pass 1 draws the icon, pass 2 embeds it
$stage = Join-Path $work "stage.exe"
$ico = Join-Path $work "fragdex.ico"
Invoke-Csc $stage @()
Start-Process -FilePath $stage -ArgumentList "--write-icon", "`"$ico`"" -Wait -NoNewWindow
if (-not (Test-Path $ico)) { throw "icon was not written" }
Invoke-Csc $out @("/win32icon:$ico", "/win32manifest:$(Join-Path $src 'app.manifest')")
Remove-Item -Recurse -Force $work
Write-Host ("Built {0} ({1:N0} KB, v{2})" -f $out, ((Get-Item $out).Length / 1KB), $version)
