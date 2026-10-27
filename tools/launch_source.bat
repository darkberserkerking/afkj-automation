@echo off
rem ============================================================
rem  AFKJ - launch from SOURCE (direct, no elevation)
rem
rem  Why no elevation attempt:
rem    This machine's policy DENIES UAC elevation for this account.
rem    Start-Process -Verb RunAs returns exit=1 WITHOUT any prompt,
rem    so an elevation attempt just aborts the launcher.
rem    The app runs fine without admin (it only logs a warning).
rem
rem  Log: %TEMP%\afkj_launch.log
rem  NOTE: keep this file ASCII-only (non-ASCII breaks cmd parsing).
rem ============================================================
chcp 65001 >nul
title AFKJ Source Mode
setlocal

set "LOG=%TEMP%\afkj_launch.log"
echo [%DATE% %TIME%] launch start>>"%LOG%"

cd /d "C:\Users\x9502\WorkBuddy\20260320211537"

set "PYW=D:\anaconda3\envs\afk_gpu\pythonw.exe"
if not exist "%PYW%" (
    echo [ERROR] Python env not found: %PYW%
    echo         check conda env "afk_gpu"
    echo [%DATE% %TIME%] ERROR pythonw missing>>"%LOG%"
    pause
    exit /b 1
)
if not exist "afk_gui.py" (
    echo [ERROR] afk_gui.py not found in: %CD%
    echo [%DATE% %TIME%] ERROR afk_gui.py missing>>"%LOG%"
    pause
    exit /b 1
)

echo Starting AFKJ (source mode)...
echo [%DATE% %TIME%] starting pythonw>>"%LOG%"
start "" "%PYW%" afk_gui.py
echo [%DATE% %TIME%] started>>"%LOG%"
exit /b 0
