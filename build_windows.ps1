param(
    [string]$OutputDir = ".\dist_gui_agent_20260908"
)

$ErrorActionPreference = "Stop"
$env:NUITKA_CACHE_DIR = (Join-Path $PSScriptRoot ".nuitka_cache")

python -m nuitka `
    --onefile `
    --enable-plugin=tk-inter `
    --windows-console-mode=hide `
    --output-filename=bv2008_gui.exe `
    --output-dir=$OutputDir `
    --include-package=bv2008_gui `
    --include-module=xlrd `
    --include-module=openpyxl `
    --include-module=qrcode `
    --include-module=PIL.ImageTk `
    .\bv_gui.py

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Output "Built: $OutputDir\bv2008_gui.exe"
