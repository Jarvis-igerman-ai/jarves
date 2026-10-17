@echo off
rem Export the current-round review screenshots into previews\r5\ (finish C). Older rounds stay in previews\r3\ etc.
rem Set GODOT_EXE to override the winget-installed Godot 4.7.x.
setlocal
if "%GODOT_EXE%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine.GodotEngine_*") do set "GODOT_EXE=%%D\Godot_v4.7.2-stable_win64.exe"
set "OUT=%~dp0previews\r5"
if not exist "%OUT%" mkdir "%OUT%"
call :shot r5_front "--view=front"
call :shot r5_side "--view=side"
call :shot r5_rear "--view=rear"
call :shot r5_3q "--view=three_quarter"
call :shot r5_top "--view=top"
call :shot r5_interior "--hide-shell" "--show-reserved"
call :shot r5_interior_side "--hide-shell" "--view=side"
call :shot r5_exploded_3q "--exploded" "--view=three_quarter"
call :shot r5_exploded_side "--exploded" "--view=side"
call :shot r5_exploded_front "--exploded" "--view=front"
call :shot r5_led_blank_closeup "--view=led_closeup"
call :shot r5_led_ring_closeup "--view=led_closeup" "--led-ring"
set "MISSING=0"
for %%N in (r5_front r5_side r5_rear r5_3q r5_top r5_interior r5_interior_side r5_exploded_3q r5_exploded_side r5_exploded_front r5_led_blank_closeup r5_led_ring_closeup) do (
  if exist "%OUT%\%%N.png" (
    for %%F in ("%OUT%\%%N.png") do if %%~zF GTR 0 (echo OK      %%N.png %%~zF bytes) else (echo EMPTY   %%N.png & set "MISSING=1")
  ) else (echo MISSING %%N.png & set "MISSING=1")
)
if "%MISSING%"=="1" exit /b 1
exit /b 0

:shot
if exist "%OUT%\%~1.png" del "%OUT%\%~1.png"
set "A=%~2 %~3 %~4"
start "" /wait "%GODOT_EXE%" --path "%~dp0." --resolution 1280x800 -- --shot="%OUT%\%~1.png" %A%
exit /b 0
