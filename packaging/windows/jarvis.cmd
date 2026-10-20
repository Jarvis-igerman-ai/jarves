@echo off
rem JARVES (installed copy). Same commands as the developer jarvis.cmd, without uv or a Python install:
rem   jarvis                start JARVES (first time: the setup wizard), live log in this window
rem   jarvis setup | key | stop | status | doctor | dashboard | update | features | logs -f | --version
rem Your data (memory, settings, keys, logs) lives in %LOCALAPPDATA%\jarves, not in this folder.
setlocal
set "JARVES_APP=%~dp0"
set "JARVES_FACE_EXE=%~dp0face\JARVES Face.exe"
rem -I: isolated (no PYTHONPATH / user site-packages from this PC can change the bundled Core)
set J="%~dp0python\python.exe" -I -X utf8 -m jarves
set "FIRST=%~1"
if "%FIRST%"=="" goto launch
if not "%FIRST:~0,1%"=="-" ( %J% %* & exit /b )
if "%FIRST%"=="--version" ( %J% %* & exit /b )
if "%FIRST%"=="--help" ( %J% %* & exit /b )
if "%FIRST%"=="-h" ( %J% %* & exit /b )
:launch
%J% status >nul 2>&1
if not "%ERRORLEVEL%"=="1" (
  echo JARVES is already running: opening the room and following its log.
  if exist "%JARVES_FACE_EXE%" start "" "%JARVES_FACE_EXE%"
  %J% logs -f --until-stopped %*
  exit /b
)
%J% setup --if-needed || ( pause & exit /b 1 )
%J% run --desktop %*
