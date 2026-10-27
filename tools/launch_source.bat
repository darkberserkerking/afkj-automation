@echo off
rem ============================================================
rem  AFKJ - launch from SOURCE (no exe, updates come via git pull)
rem  Python: D:/anaconda3/envs/afk_gpu
rem ============================================================
chcp 65001 >nul
title AFKJ Source Mode
cd /d "C:/Users/x9502/WorkBuddy/20260320211537"

set "PYW=D:/anaconda3/envs/afk_gpu/pythonw.exe"

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

echo Starting AFKJ (source mode)...
start "" "%PYW%" afk_gui.py
exit /b 0
