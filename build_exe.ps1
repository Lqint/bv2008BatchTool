$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt

python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name bv2008BatchTool `
  bv_gui.py

Write-Host ""
Write-Host "Build complete: dist\bv2008BatchTool\bv2008BatchTool.exe"
