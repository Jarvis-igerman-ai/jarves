@echo off
rem Headless import/runtime check: prints CHECK RESULT and exits non-zero on failure.
rem --quit-after guards against a script error leaving Godot idle; success requires the PASS line.
if "%GODOT_EXE%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine.GodotEngine_*") do set "GODOT_EXE=%%D\Godot_v4.7.2-stable_win64_console.exe"
set "LOG=%TEMP%\jarves_viewer_check.txt"
"%GODOT_EXE%" --headless --path "%~dp0." --quit-after 3000 -- --check > "%LOG%" 2>&1
type "%LOG%"
findstr /C:"CHECK RESULT: PASS" "%LOG%" >nul || exit /b 1
exit /b 0
