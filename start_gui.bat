@echo off
setlocal
cd /d "%~dp0"
echo Starting bv2008 GUI...
python -X utf8 bv_gui.py
if errorlevel 1 (
  echo.
  echo GUI failed to start. If bv_gui_error.log exists, please open it.
  echo.
  pause
)
