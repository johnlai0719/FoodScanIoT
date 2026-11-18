@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Competition: Database / Vision / Cloud / Dashboard ===
docker compose up -d --build --wait --wait-timeout 180
if errorlevel 1 (
  echo Startup failed. Check Docker Desktop competition logs.
) else (
  echo Ready: http://127.0.0.1:8787
)
pause
