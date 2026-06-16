@echo off
cd /d "%~dp0"

echo === FoodScan Expo Launcher ===
echo.

REM Add firewall rule for port 8085
netsh advfirewall firewall add rule name="Expo Metro 8085" protocol=TCP dir=in localport=8085 action=allow profile=any > nul 2>&1

REM Detect Tailscale IP first, fall back to Wi-Fi
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-NetIPAddress -InterfaceAlias 'Tailscale' -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress"`) do set EXPO_IP=%%I

if not "%EXPO_IP%"=="" (
  echo Using Tailscale IP: %EXPO_IP%
) else (
  for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-NetIPAddress -InterfaceAlias 'Wi-Fi' -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress"`) do set EXPO_IP=%%I
  echo Using Wi-Fi IP: %EXPO_IP%
)

set REACT_NATIVE_PACKAGER_HOSTNAME=%EXPO_IP%
echo Port: 8085
echo.

echo Starting Expo on %EXPO_IP%:8085 ...
echo Press w to open web, Ctrl+C to stop.
echo.
call npx expo start --port 8085 --host lan

pause