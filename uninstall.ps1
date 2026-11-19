# Remove the Nord Buds widget for the current user.
#   powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\NordBudsWidget\uninstall.ps1"
# Add -KeepSettings to keep %APPDATA%\NordBudsWidget (position, log, widget browser profile).
param([switch]$KeepSettings)

Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'nordbuds_widget' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-CimInstance Win32_Process -Filter "Name='msedge.exe' OR Name='chrome.exe'" |
    Where-Object { $_.CommandLine -match 'NordBudsWidget\\window' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

foreach ($dir in 'Desktop', 'Programs', 'Startup') {
    $p = Join-Path ([Environment]::GetFolderPath($dir)) 'Nord Buds Widget.lnk'
    if (Test-Path $p) { Remove-Item $p -Force; "removed $p" }
}
if (-not $KeepSettings) { Remove-Item (Join-Path $env:APPDATA 'NordBudsWidget') -Recurse -Force -ErrorAction SilentlyContinue; "removed settings" }

# the app folder holds this script, so delete it after we exit
$app = Join-Path $env:USERPROFILE 'NordBudsWidget'
Start-Process -WindowStyle Hidden cmd.exe -ArgumentList "/c timeout /t 2 >nul & rmdir /s /q `"$app`""
"Nord Buds widget uninstalled."
