@echo off
echo ========================================================
echo Starting Makima Brain Server (port 8080)...
echo ========================================================
cd /d "%~dp0"
python -m apps.brain.main
pause
