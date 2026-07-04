@echo off
setlocal
title Dodi Store Bot - Dev Auto Reload
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev-reload.ps1"

endlocal
pause
