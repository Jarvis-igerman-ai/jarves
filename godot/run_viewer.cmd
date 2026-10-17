@echo off
rem Launch the JARVIS review viewer. Set GODOT_EXE to override the winget-installed Godot 4.7.x.
if "%GODOT_EXE%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine.GodotEngine_*") do set "GODOT_EXE=%%D\Godot_v4.7.2-stable_win64.exe"
start "" "%GODOT_EXE%" --path "%~dp0."
