@echo off
chcp 65001 > nul
echo Starting Telegram Business Tracker Bot...
cd /d %~dp0

call venv\Scripts\activate.bat

python -m core.bot

if errorlevel 1 (
    echo.
    echo Bot crashed. Check the console output above for details.
)

pause