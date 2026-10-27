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
rem  capture stdout+stderr to the log so a failure is never silent.
rem  -Wait: wait for the elevated instance to finish launching.
rem  try/catch: if UAC is cancelled we must NOT die - fall back to a
rem  normal (non-admin) launch so the app always opens.
echo [%DATE% %TIME%] cmd: elevate via Start-Process -Verb RunAs -Wait>>"%LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath '%~f0' -Verb RunAs -Wait -ErrorAction Stop; exit 0 } catch { Write-Output ('ELEVATE-ERROR: ' + $_.Exception.Message); exit 1 }" >>"%LOG%" 2>&1
if not errorlevel 1 (
    echo [%DATE% %TIME%] elevation ok - elevated instance handles launch>>"%LOG%"
    exit /b
)
echo [%DATE% %TIME%] elevation FAILED/CANCELLED - fallback to normal launch>>"%LOG%"
echo.
echo [!] Elevation failed or was cancelled - starting WITHOUT admin.
echo [!] Game control may not work. Details: %LOG%
echo.
goto launch

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
