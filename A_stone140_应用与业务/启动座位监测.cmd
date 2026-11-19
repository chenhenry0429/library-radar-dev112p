@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
    py -3 live_monitor.py --speed 5
) else (
    python live_monitor.py --speed 5
)
pause
