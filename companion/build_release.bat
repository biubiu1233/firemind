@echo off
cd /d "%~dp0"
title FireMind Build
echo ========================================
echo FireMind companion pack (Windows)
echo Dir: %CD%
echo ========================================
echo.

set "PY=py -3"
%PY% -c "import sys" 2>nul
if errorlevel 1 set "PY=python"
%PY% -c "import sys" 2>nul
if errorlevel 1 (
  echo [ERROR] No Python. Install Python 3.10+ with Add to PATH.
  goto end
)

echo [1/4] pip install...
%PY% -m pip install --upgrade pip
%PY% -m pip install pyinstaller -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip failed. See messages above.
  goto end
)

echo [2/4] clean old build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist FireMindCompanion.spec del /q FireMindCompanion.spec

echo [3/4] PyInstaller (1-3 min)...
%PY% -m PyInstaller --noconfirm --clean --onefile --noconsole --name FireMindCompanion --hidden-import=keyboard --hidden-import=mss --hidden-import=PIL.Image --collect-all mss firemind_overlay.py
if errorlevel 1 (
  echo [ERROR] PyInstaller failed. See messages above.
  goto end
)

if not exist "dist\FireMindCompanion.exe" (
  echo [ERROR] dist\FireMindCompanion.exe missing.
  if exist dist dir dist
  goto end
)

echo [4/4] copy to release\
if not exist release mkdir release
copy /Y "dist\FireMindCompanion.exe" "release\"
copy /Y "config.example.json" "release\config.json"
copy /Y "群友安装说明.txt" "release\"

echo.
echo ===== OK =====
dir /b release
echo Edit release\config.json api_url then zip the release folder.
echo.

:end
echo Press any key to close...
pause >nul
