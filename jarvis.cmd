@echo off
rem jarvis                                start JARVES here, with live logs in this terminal (Ctrl+C stops)
rem jarvis --log-level debug             levels: error, warning, info, debug, verbose
rem jarvis --module voice,wake --hide link   show only / hide modules
rem jarvis start [--show]                JARVES in the background: no window, tray icon, "Hey Jarvis" (returns at once)
rem jarvis stop ^| status ^| show ^| hide    stop it / its four states / open or close the window (Core keeps running)
rem jarvis autostart on ^| off ^| status     start invisibly when you log in to Windows (default off)
rem jarvis setup ^| key ^| logs -f ^| ^<any Core command^>
rem jarvis dashboard                      open the local dashboard (home, conversations, logs, costs, memory, settings)
rem Closing the window only hides JARVES; Quit in the tray (or jarvis stop) stops him.
setlocal
set "C=%~dp0core"
rem --no-sync: never reinstall while a Core is running (its jarves.exe is locked); install happens on start below
set "J=uv --directory "%C%" run --no-sync jarves"
if not exist "%C%\.venv" uv --directory "%C%" sync --inexact -q --extra voice --extra tray
set "FIRST=%~1"
if /i "%FIRST%"=="--help" ( %J% %* & exit /b )
if /i "%FIRST%"=="-h" ( %J% %* & exit /b )
if /i "%FIRST%"=="--version" ( %J% %* & exit /b )
if "%FIRST%"=="" goto launch
if /i "%FIRST%"=="start" goto start
if not "%FIRST%"=="" if not "%FIRST:~0,1%"=="-" ( %J% %* & exit /b )
:launch
%J% status --brief >nul 2>&1
if not "%ERRORLEVEL%"=="1" (
  echo JARVES is already running: opening the room and following its log.
  %J% show
  %J% logs -f --until-stopped %*
  exit /b
)
uv --directory "%C%" sync --inexact -q --extra voice --extra tray || ( echo Could not install JARVES packages. & pause & exit /b 1 )
%J% setup --if-needed || ( pause & exit /b 1 )
%J% run --desktop %*
exit /b
:start
%J% status --brief >nul 2>&1
if "%ERRORLEVEL%"=="1" (
  uv --directory "%C%" sync --inexact -q --extra voice --extra tray || ( echo Could not install JARVES packages. & exit /b 1 )
  %J% setup --if-needed || exit /b 1
)
%J% %*
