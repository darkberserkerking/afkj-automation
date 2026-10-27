@echo off
rem ============================================================
rem  AFKJ - launch from SOURCE, with ADMIN elevation (UAC prompt)
rem  Updates: git pull (no exe / no Release needed)
rem ============================================================
chcp 65001 >nul
title AFKJ Source Mode (Admin)
setlocal

rem ---- 1) self-elevate: if not admin, relaunch this script as admin ----
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

rem ---- 2) now running as administrator ----
cd /d "C:\Users\x9502\WorkBuddy\20260320211537"

set "PYW=D:\anaconda3\envs\afk_gpu\pythonw.exe"
if not exist "%PYW%" (
    echo [ERROR] Python env not found: %PYW%
    echo         check conda env "afk_gpu"
    pause
    exit /b 1
)
if not exist "afk_gui.py" (
    echo [ERROR] afk_gui.py not found in: %CD%
    pause
    exit /b 1
)

echo Starting AFKJ (source mode, ADMIN)...
start "" "%PYW%" afk_gui.py
exit /b 0
