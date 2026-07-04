@echo off
setlocal
title Dodi Store Bot - admin dashboard
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo [ERROR] Setup hasn't been run yet.
    echo Double-click setup.bat first.
    pause
    exit /b 1
)
if not exist .env (
    echo [ERROR] No .env file found.
    echo Double-click setup.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
echo Open http://127.0.0.1:8088 in your browser.
echo Press Ctrl+C to stop the dashboard.
python dashboard.py

endlocal
pause
