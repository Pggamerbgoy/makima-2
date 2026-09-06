@echo off
title Makima Brain Server (Interactive Mode)
echo ===================================================
echo   Starting Makima Brain Server in Interactive Mode
echo   (Headed Browser will be VISIBLE on your desktop)
echo ===================================================
cd /d "%~dp0"

for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":8080" ^| findstr "LISTENING"') do (
    echo [Makima] Freeing port 8080 from previous instance (PID: %%p)...
    taskkill /F /PID %%p >nul 2>&1
)

:: Disable QuickEdit Mode in this console session so mouse clicks never freeze the event loop
powershell -NoProfile -Command "$h=[System.IntPtr]([System.Runtime.InteropServices.Marshal]::GetLastWin32Error()); Add-Type -MemberDefinition '[DllImport(\"kernel32.dll\")] public static extern IntPtr GetStdHandle(int nStdHandle); [DllImport(\"kernel32.dll\")] public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode); [DllImport(\"kernel32.dll\")] public static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);' -Name Win32Console -Namespace Win32; $h=([Win32.Win32Console]::GetStdHandle(-10)); $m=0; [Win32.Win32Console]::GetConsoleMode($h, [ref]$m); [Win32.Win32Console]::SetConsoleMode($h, ($m -band -bnot 0x0040) -bor 0x0080)" >nul 2>&1

python -m apps.brain.main
pause
