@echo off
rem Export the room-simulation previews into godot\previews\room\ and fail (exit 1) if any is missing or empty.
rem Set GODOT_EXE to override the winget-installed Godot 4.7.x.
setlocal
if "%GODOT_EXE%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine.GodotEngine_*") do set "GODOT_EXE=%%D\Godot_v4.7.2-stable_win64.exe"
set "PRJ=%~dp0.."
set "OUT=%PRJ%\previews\room"
if not exist "%OUT%" mkdir "%OUT%"
call :shot room_day "--cam=desk"
call :shot room_evening "--cam=desk" "--evening"
call :shot room_closeup "--cam=closeup"
call :shot room_sofa "--cam=sofa"
set "MISSING=0"
for %%N in (room_day room_evening room_closeup room_sofa) do (
  if exist "%OUT%\%%N.png" (
    for %%F in ("%OUT%\%%N.png") do if %%~zF GTR 0 (echo OK      %%N.png %%~zF bytes) else (echo EMPTY   %%N.png & set "MISSING=1")
  ) else (echo MISSING %%N.png & set "MISSING=1")
)
if "%MISSING%"=="1" exit /b 1
exit /b 0

:shot
if exist "%OUT%\%~1.png" del "%OUT%\%~1.png"
start "" /wait "%GODOT_EXE%" --path "%PRJ%" --resolution 1280x800 -- --no-core --hide-ui --shot="%OUT%\%~1.png" %~2 %~3
exit /b 0
