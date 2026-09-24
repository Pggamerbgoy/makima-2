@echo off
title Makima Chat UI (Vite Dev Server)
echo ========================================================
echo   Starting Makima Chat UI on http://localhost:5173
echo ========================================================
cd /d "%~dp0apps\chat_ui"
npm run dev
pause
