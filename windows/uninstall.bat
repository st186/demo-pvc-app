@echo off
rem Removes a PVC Studio installed by install.bat (the Setup.exe version
rem uninstalls from Windows Settings > Apps instead).
setlocal
set "TARGET=%~dp0"
echo This removes PVC Studio from %TARGET%
choice /m "Continue"
if errorlevel 2 exit /b 0
del "%USERPROFILE%\Desktop\PVC Studio.lnk" 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\PVC Studio.lnk" 2>nul
rmdir /s /q "%LOCALAPPDATA%\PVC Studio" 2>nul
cd /d "%TEMP%"
start "" /b cmd /c "timeout /t 2 >nul & rmdir /s /q ""%TARGET%"""
echo PVC Studio has been removed.
