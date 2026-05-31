$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt

$doc = Get-ChildItem -Path . -Filter "*.png" | Select-Object -First 1
if (-not $doc) {
  throw "No PNG support document found in project root."
}
Copy-Item -LiteralPath $doc.FullName -Destination ".\support_doc.png" -Force

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name bv2008BatchTool `
  --add-data "support_doc.png;." `
  bv_gui.py

if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

Remove-Item ".\support_doc.png" -Force

Write-Host ""
Write-Host "Build complete: dist\bv2008BatchTool.exe"
