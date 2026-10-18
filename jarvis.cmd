@echo off
rem jarvis          start JARVES (asks how to connect the first time)
rem jarvis setup    change ChatGPT / API key choice
rem jarvis stop     stop
rem jarvis <other>  any Core command, e.g. jarvis status, jarvis ask "מה השעה"
set "J=uv --directory "%~dp0core" run jarves"
if not "%~1"=="" ( %J% %* & exit /b )
%J% setup --if-needed || ( pause & exit /b 1 )
start "JARVES Core" /min cmd /c "%~dp0core\run_core.cmd"
timeout /t 4 /nobreak >nul
call "%~dp0godot\run_face.cmd"
echo JARVES is running. Hold SPACE to talk, or type.  Stop: jarvis stop
