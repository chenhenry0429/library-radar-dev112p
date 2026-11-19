@echo off
setlocal
cd /d "%~dp0"
python -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 goto python_ready
py -3.12 -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 goto launcher_ready
echo Python 3.12 or newer was not found. Install Python or make python.exe available in PATH.
pause
exit /b 1

:python_ready
python live_monitor.py --speed 5 %*
goto done

:launcher_ready
py -3.12 live_monitor.py --speed 5 %*

:done
pause
