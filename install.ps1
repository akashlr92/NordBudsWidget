# Install / update the Nord Buds widget for the current Windows user (no admin needed).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
#
#   - checks prerequisites (Windows 10/11, Python 3.9+, Microsoft Edge or Chrome)
#   - copies the app to %USERPROFILE%\NordBudsWidget
#     (settings, log and the widget's browser profile live in %APPDATA%\NordBudsWidget)
#   - creates Desktop, Start menu and Startup (auto-start at sign-in) shortcuts with the icon
#   - refreshes Explorer's icons and starts the widget
#
# Switches:  -NoStartup   don't auto-start at sign-in
#            -NoLaunch    don't start the widget after installing
param(
    [string]$Source = $PSScriptRoot,
    [switch]$NoStartup,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
function Fail($msg) { Write-Host "INSTALL FAILED: $msg" -ForegroundColor Red; exit 1 }
function Ok($msg) { Write-Host "  [ok] $msg" -ForegroundColor Green }

Write-Host "Nord Buds widget installer" -ForegroundColor Cyan

# ---------------------------------------------------------------- prerequisites
if ([Environment]::OSVersion.Version.Major -lt 10) { Fail "Windows 10 or 11 is required." }
Ok "Windows $([Environment]::OSVersion.Version)"

$files = 'nordbuds_widget.pyw', 'nordbuds_ui.html', 'bt_audio.py', 'desk_colors.py', 'nordbuds.ico', 'nordbuds.png'
foreach ($f in $files) { if (-not (Test-Path (Join-Path $Source $f))) { Fail "missing file '$f' next to install.ps1 (unzip the whole package first)." } }
Ok "package files present"

# Python: prefer pythonw.exe on PATH, else ask the py launcher where Python lives
$pyw = $null
$cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { $pyw = $cmd.Source }
if (-not $pyw -and (Get-Command py.exe -ErrorAction SilentlyContinue)) {
    $exe = (& py.exe -3 -c "import sys; print(sys.executable)" 2>$null)
    if ($exe) { $cand = Join-Path (Split-Path $exe) 'pythonw.exe'; if (Test-Path $cand) { $pyw = $cand } }
}
if (-not $pyw) { Fail "Python 3.9+ not found. Install it (e.g. 'winget install -e --id Python.Python.3.12'), then run this again." }
$python = Join-Path (Split-Path $pyw) 'python.exe'
$ver = & $python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]'3.9') { Fail "Python $ver found at $python, but 3.9 or newer is required." }
Ok "Python $ver ($pyw)"

$browser = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
             "$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") |
           Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { Fail "Microsoft Edge (or Google Chrome) is required to draw the widget." }
Ok "browser engine: $browser"

# ---------------------------------------------------------------- copy files
$app = Join-Path $env:USERPROFILE 'NordBudsWidget'   # not AppData: Explorer may refuse shortcut icons from there

Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'nordbuds_widget' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-CimInstance Win32_Process -Filter "Name='msedge.exe' OR Name='chrome.exe'" |
    Where-Object { $_.CommandLine -match 'NordBudsWidget\\window' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

New-Item -ItemType Directory -Force $app | Out-Null
foreach ($f in $files + 'uninstall.ps1') {
    $p = Join-Path $Source $f
    if (Test-Path $p) { Copy-Item $p $app -Force }
}
# a fresh icon file name makes Explorer drop any cached (blank) icon for the shortcuts
Get-ChildItem $app -Filter 'nordbuds_v*.ico' | Remove-Item -Force
$icon = Join-Path $app ("nordbuds_v{0}.ico" -f (Get-Date -Format 'yyyyMMddHHmmss'))
Copy-Item (Join-Path $app 'nordbuds.ico') $icon
Ok "installed files to $app"

# ---------------------------------------------------------------- shortcuts
$ws = New-Object -ComObject WScript.Shell
$links = [ordered]@{
    (Join-Path ([Environment]::GetFolderPath('Desktop'))  'Nord Buds Widget.lnk') = ''
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Nord Buds Widget.lnk') = ''
}
$startupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'Nord Buds Widget.lnk'
if ($NoStartup) { if (Test-Path $startupLink) { Remove-Item $startupLink -Force } }
else { $links[$startupLink] = ' --startup' }
foreach ($path in $links.Keys) {
    if (Test-Path $path) { Remove-Item $path -Force }
    $l = $ws.CreateShortcut($path)
    $l.TargetPath = $pyw
    $l.Arguments = "`"$app\nordbuds_widget.pyw`"" + $links[$path]
    $l.WorkingDirectory = $app
    $l.IconLocation = "$icon,0"
    $l.Description = 'Control OnePlus Nord Buds earbuds'
    $l.Save()
    Ok "shortcut: $path"
}

Add-Type -Namespace NBInstall -Name Shell -MemberDefinition '[DllImport("shell32.dll")] public static extern void SHChangeNotify(int e, uint f, IntPtr a, IntPtr b);'
[NBInstall.Shell]::SHChangeNotify(0x08000000, 0x1000, [IntPtr]::Zero, [IntPtr]::Zero)   # SHCNE_ASSOCCHANGED
Start-Process ie4uinit.exe -ArgumentList '-show' -Wait -ErrorAction SilentlyContinue
Ok "Explorer icons refreshed"

# ---------------------------------------------------------------- earbuds paired?
$paired = Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -like 'BTHENUM\{0000079A-D102-11E1-9B23-00025B00A5A5}*' }
if ($paired) { Ok "OnePlus/OPPO earbuds are paired with this PC" }
else { Write-Host "  [!] No OnePlus/OPPO earbuds are paired yet. Pair them in Settings > Bluetooth; the widget connects automatically." -ForegroundColor Yellow }

# ---------------------------------------------------------------- launch
if (-not $NoLaunch) {
    Start-Process -FilePath $pyw -ArgumentList "`"$app\nordbuds_widget.pyw`"" -WorkingDirectory $app
    Ok "widget started (it appears in ~4 seconds, top-right of the screen)"
}
Write-Host "Done. Log: $env:APPDATA\NordBudsWidget\widget.log" -ForegroundColor Cyan
