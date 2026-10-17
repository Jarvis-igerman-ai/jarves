@echo off
rem Headless import/runtime check: prints CHECK RESULT and exits non-zero on failure.
if "%GODOT_EXE%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine.GodotEngine_*") do set "GODOT_EXE=%%D\Godot_v4.7.2-stable_win64_console.exe"
"%GODOT_EXE%" --headless --path "%~dp0." -- --check
exit /b %ERRORLEVEL%
