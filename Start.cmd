@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Install Python 3.12 or later, then follow README.md setup.
  pause
  exit /b 1
)
.venv\Scripts\python.exe run.py
pause
