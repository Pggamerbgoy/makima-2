@echo off
title Makima Brain Server (Interactive Mode)
echo ===================================================
echo   Starting Makima Brain Server in Interactive Mode
echo   (Headed Browser will be VISIBLE on your desktop)
echo ===================================================
cd /d "%~dp0"
python -m apps.brain.main
pause
