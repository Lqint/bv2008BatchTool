$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt

if (-not (Test-Path ".\support_doc.png")) {
  throw "No support_doc.png found in project root."
}
if (-not (Test-Path ".\logo.ico")) {
  throw "No logo.ico found in project root."
}

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name bv2008BatchTool `
  --icon=logo.ico `
  --add-data "support_doc.png;." `
  --add-data "logo.ico;." `
  bv_gui.py

if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Build complete: dist\bv2008BatchTool.exe"
