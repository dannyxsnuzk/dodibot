@echo off
setlocal
title Batman Bot
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
echo Press Ctrl+C to stop the bot.
python bot.py

endlocal
pause
