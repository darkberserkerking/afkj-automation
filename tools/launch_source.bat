@echo off
rem ============================================================
rem  AFKJ - launch from SOURCE (no elevation attempt)
rem
rem  Why no elevation attempt here:
rem    On some machines UAC elevation from a .bat is denied
rem    silently (no prompt, body never runs), so trying it just
rem    aborts the launcher. For an elevated start use the small
rem    launcher in tools/launch_source_admin.py, which carries a
rem    requireAdministrator manifest.
rem
rem  Repo-relative: no machine paths. Python discovery order:
rem    1) AFKJ_PYTHON environment variable
rem    2) pythonw.exe on PATH
rem    3) every env under the conda root found via "where conda"
rem  A candidate is accepted only if it imports the project deps.
rem
rem  Log: %TEMP%\afkj_launch.log
rem  NOTE: keep this file ASCII-only (non-ASCII breaks cmd parsing).
rem ============================================================
chcp 65001 >nul
title AFKJ Source Mode
setlocal enabledelayedexpansion

set "LOG=%TEMP%\afkj_launch.log"
echo [%DATE% %TIME%] launch start>>"%LOG%"

rem ---- repo root = parent of this file's directory --------------
cd /d "%~dp0.."
if not exist "afk_gui.py" goto :no_repo

set "PROBE=import customtkinter, pyautogui, onnxruntime, cv2, win32gui, pyclipper, shapely"

rem ---- 1) explicit override -------------------------------------
if defined AFKJ_PYTHON if exist "%AFKJ_PYTHON%" (
    "%AFKJ_PYTHON%" -c "%PROBE%" >nul 2>&1
    if not errorlevel 1 (
        set "PYW=%AFKJ_PYTHON%"
        goto :launch
    )
    echo [WARN] AFKJ_PYTHON lacks some packages, falling back to auto-detect
)

rem ---- 2) pythonw.exe from PATH --------------------------------
for %%C in (pythonw.exe) do if not "%%~$PATH:C"=="" (
    "%%~$PATH:C" -c "%PROBE%" >nul 2>&1
    if not errorlevel 1 (
        set "PYW=%%~$PATH:C"
        goto :launch
    )
)

rem ---- 3) conda envs -------------------------------------------
set "CONDAROOT="
for /f "delims=" %%P in ('where conda 2^>nul') do if not defined CONDAROOT set "CONDAROOT=%%~dpP..\.."
if defined CONDAROOT for %%F in ("!CONDAROOT!") do set "CONDAROOT=%%~fF"
if defined CONDAROOT if exist "!CONDAROOT!\envs" (
    for /d %%E in ("!CONDAROOT!\envs\*") do (
        if exist "%%E\pythonw.exe" (
            "%%E\pythonw.exe" -c "%PROBE%" >nul 2>&1
            if not errorlevel 1 (
                set "PYW=%%E\pythonw.exe"
                goto :launch
            )
        )
    )
)

echo.
echo [ERROR] No Python with the project dependencies was found.
echo.
echo Create one and install them:
echo.
echo   conda create -n afk python=3.12 -y
echo   conda activate afk
echo   pip install customtkinter opencv-python onnxruntime Pillow pywin32 keyboard pyautogui pyclipper shapely numpy
echo.
echo Then activate that env before running this file, or point this
echo variable at its pythonw.exe:
echo.
echo   set AFKJ_PYTHON=C:\path\to\env\pythonw.exe
echo.
echo [%DATE% %TIME%] ERROR no usable python>>"%LOG%"
pause
exit /b 1

:launch
echo Starting AFKJ (source mode)...
echo   python: !PYW!
echo   repo  : %CD%
echo [%DATE% %TIME%] starting pythonw: !PYW!>>"%LOG%"
start "" "!PYW!" afk_gui.py
echo [%DATE% %TIME%] started>>"%LOG%"
exit /b 0

:no_repo
echo [ERROR] afk_gui.py not found in: %CD%
echo [%DATE% %TIME%] ERROR afk_gui.py missing>>"%LOG%"
pause
exit /b 1
