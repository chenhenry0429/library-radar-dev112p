@echo off
setlocal
pushd "%~dp0"
set "RADAR_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%RADAR_PY%" goto bundled
where py >nul 2>nul
if not errorlevel 1 goto launcher
where python >nul 2>nul
if not errorlevel 1 goto systempython
echo Python 3.10+ with NumPy is required. See README.
set "RADAR_EXIT=2"
goto finish
:bundled
"%RADAR_PY%" -X utf8 %*
set "RADAR_EXIT=%ERRORLEVEL%"
goto finish
:launcher
py -3 -X utf8 %*
set "RADAR_EXIT=%ERRORLEVEL%"
goto finish
:systempython
python -X utf8 %*
set "RADAR_EXIT=%ERRORLEVEL%"
:finish
popd
exit /b %RADAR_EXIT%
