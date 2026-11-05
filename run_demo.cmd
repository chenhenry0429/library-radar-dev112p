@echo off
setlocal
chcp 65001 >nul
set "RADAR_DEMO=%~dp0DEMO_ONLY\demo_%RANDOM%_%RANDOM%"
call "%~dp0run_python.cmd" "%~dp0radar_offline.py" demo --output "%RADAR_DEMO%"
if errorlevel 1 goto failed
call "%~dp0run_python.cmd" "%~dp0radar_offline.py" pack "%RADAR_DEMO%\20261103_PDEMO_S01_lifecycle_01" --output "%RADAR_DEMO%\DEMO_ONLY_transfer.zip"
if errorlevel 1 goto failed
echo.
echo Demo completed. All generated data is SYNTHETIC ONLY.
echo Output: "%RADAR_DEMO%"
pause
exit /b 0
:failed
echo.
echo Failed. See the message above and README.
pause
exit /b 2
