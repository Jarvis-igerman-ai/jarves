@echo off
rem jarvis                                start JARVES here, with live logs in this terminal (Ctrl+C stops)
rem jarvis --log-level debug             levels: error, warning, info, debug, verbose
rem jarvis --module voice,wake --hide link   show only / hide modules
rem jarvis setup ^| stop ^| status ^| key ^| logs -f ^| ^<any Core command^>
rem jarvis dashboard                      open the local dashboard (conversations, logs, costs, memory, settings)
setlocal
set "C=%~dp0core"
rem --no-sync: never reinstall while a Core is running (its jarves.exe is locked); install happens on start below
set "J=uv --directory "%C%" run --no-sync jarves"
if not exist "%C%\.venv" uv --directory "%C%" sync -q --extra voice
set "FIRST=%~1"
if "%FIRST%"=="" goto launch
if not "%FIRST%"=="" if not "%FIRST:~0,1%"=="-" ( %J% %* & exit /b )
:launch
%J% status >nul 2>&1
if not "%ERRORLEVEL%"=="1" (
  echo JARVES is already running: opening the room and following its log.
  call "%~dp0godot\run_viewer.cmd" res://loading.tscn
  %J% logs -f --until-stopped %*
  exit /b
)
uv --directory "%C%" sync -q --extra voice || ( echo Could not install JARVES packages. & pause & exit /b 1 )
%J% setup --if-needed || ( pause & exit /b 1 )
%J% run --desktop %*
