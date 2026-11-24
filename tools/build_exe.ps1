# Build the app exe (onedir) and ship the python sources next to it.
# If the packaged exe cannot register a tray icon, the app hands off to
# pythonw using these sources, so the tray keeps working.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m PyInstaller --noconfirm --onedir --windowed --name "校园网助手" `
    --icon "assets\app.ico" --version-file "tools\version_info.txt" `
    --add-data "assets;assets" tk_app.py | Out-Null

# remove stale onefile build (single-file exe extracts to TEMP and can fail)
Remove-Item (Join-Path $root "dist" | Join-Path -ChildPath "校园网助手.exe") -Force -ErrorAction SilentlyContinue

$out = Join-Path $root "dist\校园网助手"
$src = Join-Path $out "src"
New-Item -ItemType Directory -Force $src | Out-Null
foreach ($f in @("tk_app.py", "engine.py", "portal.py", "aes128.py", "store.py", "tray.py")) {
    Copy-Item (Join-Path $root $f) $src -Force
}
Copy-Item (Join-Path $root "assets") $src -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "built: $out"
Write-Host "sources: $src"
