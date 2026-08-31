@echo off
cd /d "%~dp0"
echo Starting PowerStore Monitor...
echo.
pstore-monitor.exe %*
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% NEQ 0 (
  echo.
  echo PowerStore Monitor exited with error code %EXIT_CODE%.
  echo Check startup.log in %%LOCALAPPDATA%%\pstore-monitor\
  echo.
  pause
)
