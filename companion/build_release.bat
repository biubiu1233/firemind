@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] 安装打包依赖…
py -3 -m pip install pyinstaller -r requirements.txt -q
echo [2/3] 打包 FireMindCompanion.exe …
py -3 -m PyInstaller --onefile --noconsole --name FireMindCompanion firemind_overlay.py
if not exist "dist\FireMindCompanion.exe" exit /b 1
if not exist "release" mkdir release
copy /Y dist\FireMindCompanion.exe release\
if not exist "release\config.json" copy /Y config.example.json release\config.json
copy /Y 群友安装说明.txt release\
echo [3/3] 完成: release\FireMindCompanion.exe
echo 请编辑 release\config.json 里的 api_url 后再 zip 发给群友。
pause
