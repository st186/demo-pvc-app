@echo off
rem PVC Studio installer for Windows 10/11 (64-bit).
rem Needs no Python or admin rights: it downloads a private copy of Python and
rem all packages (~1 GB, internet required once) into your user folder, then
rem adds "PVC Studio" to the Desktop and Start menu.
setlocal
title PVC Studio installer
set "TARGET=%LOCALAPPDATA%\Programs\PVC Studio"

echo.
echo  ==========================================
echo    PVC Studio installer
echo  ==========================================
echo.
echo  Installing to: %TARGET%
echo  This downloads about 1 GB and can take 5-15 minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_env.ps1" -Source "%~dp0.." -Target "%TARGET%" -Shortcuts
if errorlevel 1 (
    echo.
    echo  Installation FAILED. Check your internet connection and run install.bat again.
    echo  It continues from where it stopped.
    pause
    exit /b 1
)

echo.
echo  Done! Starting PVC Studio...
start "" "%TARGET%\python\pythonw.exe" -m pvccard.desktop
timeout /t 5 >nul
