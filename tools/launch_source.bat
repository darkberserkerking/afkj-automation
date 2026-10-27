@echo off
rem ============================================================
rem  AFKJ - launch from SOURCE, with ADMIN elevation (UAC)
rem  Updates: git pull   (no exe / no Release needed)
rem  Log file: %TEMP%\afkj_launch.log
rem  NOTE: keep this file ASCII-only. Non-ASCII comments in a .bat
rem        get decoded as GBK by cmd and break line parsing.
rem ============================================================
chcp 65001 >nul
title AFKJ Source Mode (Admin)
setlocal

set "LOG=%TEMP%\afkj_launch.log"
echo [%DATE% %TIME%] launch start>>"%LOG%"

rem ---- 1) loop guard: once we tried to elevate, never try again ----
rem   (net session depends on the Server service; if that service is
rem    stopped it returns non-zero EVEN AS ADMIN -> endless UAC loop)
if defined AFKJ_ELEVATED goto launch
set "AFKJ_ELEVATED=1"

rem ---- 2) admin check: fltmc works without relying on any service ----
fltmc >nul 2>&1
if not errorlevel 1 goto launch

echo [%DATE% %TIME%] requesting elevation>>"%LOG%"
echo Requesting administrator privileges...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:launch
echo [%DATE% %TIME%] launching (elevated-flag=%AFKJ_ELEVATED%)>>"%LOG%"
cd /d "C:\Users\x9502\WorkBuddy\20260320211537"

set "PYW=D:\anaconda3\envs\afk_gpu\pythonw.exe"
if not exist "%PYW%" (
    echo [ERROR] Python env not found: %PYW%
    echo [%DATE% %TIME%] ERROR: pythonw missing>>"%LOG%"
    pause
    exit /b 1
)
if not exist "afk_gui.py" (
    echo [ERROR] afk_gui.py not found in: %CD%
    echo [%DATE% %TIME%] ERROR: afk_gui.py missing>>"%LOG%"
    pause
    exit /b 1
)

echo Starting AFKJ (source mode, ADMIN)...
echo [%DATE% %TIME%] starting pythonw>>"%LOG%"
start "" "%PYW%" afk_gui.py
echo [%DATE% %TIME%] started>>"%LOG%"
exit /b 0
