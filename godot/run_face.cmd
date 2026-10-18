@echo off
rem Launch the JARVES face display (connects to the running Core: core\run_core.cmd). Add -- --kiosk to hide controls.
if "%GODOT_EXE%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine.GodotEngine_*") do set "GODOT_EXE=%%D\Godot_v4.7.2-stable_win64.exe"
start "" "%GODOT_EXE%" --path "%~dp0." res://face/face_app.tscn %*
