@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv 2>nul || python -m venv .venv
  call .venv\Scripts\activate.bat
  pip install -r requirements.txt -q
) else (
  call .venv\Scripts\activate.bat
)
if not exist "config.json" copy config.example.json config.json
python firemind_overlay.py
