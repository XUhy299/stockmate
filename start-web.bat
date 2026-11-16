@echo off
setlocal
cd /d "%~dp0"
if not exist "front\dist\index.html" (
  echo Please run: npm --prefix front ci
  echo Then run:   npm --prefix front run build
  pause
  exit /b 1
)
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" main.py --web
) else (
  python main.py --web
)
if errorlevel 1 pause
