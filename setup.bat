@echo off
setlocal
title Batman Bot setup
cd /d "%~dp0"

echo.
echo ====================================================================
echo  BATMAN BOT - first-time setup
echo ====================================================================
echo.

REM 1. Find Python on PATH.
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python is not on your PATH.
    echo.
    echo Install Python 3.11 or newer from:
    echo     https://www.python.org/downloads/
    echo During the installer, tick "Add python.exe to PATH".
    echo Then double-click setup.bat again.
    echo.
    pause
    exit /b 1
)

REM 2. Create virtual environment if missing.
if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment in .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        pause
        exit /b 1
    )
)

REM 3. Install dependencies.
echo Installing dependencies (first time may take a minute) ...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Could not install dependencies.
    pause
    exit /b 1
)

REM 4. Interactive credential setup.
python setup.py
if errorlevel 1 (
    pause
    exit /b 1
)

echo.
echo ====================================================================
echo  All set. Use these next time:
echo    run.bat  - start the bot and admin dashboard
echo ====================================================================
echo.

choice /C YN /N /M "Start the bot now? [Y/N] "
if errorlevel 2 goto :end
echo.
echo Press Ctrl+C to stop the bot.
python bot.py

:end
endlocal
pause
