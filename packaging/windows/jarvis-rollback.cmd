@echo off
rem JARVES recovery (#261): the new program does not start after an update. Puts back the previous program AND your data
rem from before the update, together. Needs no working JARVES: it uses the kept old Python (rollback\python).
rem Your data is changed only after you answer Y. Close JARVES from the tray first. Usage: jarvis-rollback.cmd [data folder]
setlocal
set "APP=%~dp0"
set "OLD=%APP%rollback\python\python.exe"
set "DATA=%~1"
if "%DATA%"=="" set "DATA=%JARVES_HOME%"
if "%DATA%"=="" set "DATA=%LOCALAPPDATA%\jarves"
if not exist "%OLD%" (
  echo No previous JARVES program is kept, so it cannot be put back automatically. Install the previous version again.
  pause
  exit /b 8
)
"%OLD%" -I "%APP%installer_state.py" recover-program --home "%DATA%" --state "%APP%install-state.json" --app "%APP%."
set "RC=%ERRORLEVEL%"
if not "%RC%"=="6" goto done
set "OK="
set /p "OK=Put back the previous JARVES and your data from before the update? (Y/N): "
if /i not "%OK%"=="Y" ( echo Nothing was changed. & goto done )
"%OLD%" -I "%APP%installer_state.py" recover-program --home "%DATA%" --state "%APP%install-state.json" --app "%APP%." --approve
set "RC=%ERRORLEVEL%"
if "%RC%"=="1" echo Recovery failed and stopped safely; see the folder %APP% and ask for help.
:done
pause
exit /b %RC%
