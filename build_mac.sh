#!/bin/bash
set -e

pip install -r requirements.txt

pyinstaller \
  --noconfirm \
  --clean \
  --onefile \
  --windowed \
  --name bv2008BatchTool \
  --icon=logo.png \
  --add-data "support_doc.png:." \
  --add-data "logo.ico:." \
  bv_gui.py

echo ""
echo "Build complete: dist/bv2008BatchTool.app"
