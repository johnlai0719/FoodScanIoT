@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo === FoodScan Competition Cloud ===
python tools\start_competition_cloud.py
if errorlevel 1 (
  echo.
  echo Startup failed. See the message above and .artifacts logs.
)
pause
