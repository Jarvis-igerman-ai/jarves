@echo off
rem Export the r3 review screenshots into previews\r3\. Set GODOT_EXE to override the winget-installed Godot 4.7.x.
setlocal
if "%GODOT_EXE%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine.GodotEngine_*") do set "GODOT_EXE=%%D\Godot_v4.7.2-stable_win64.exe"
set "OUT=%~dp0previews\r3"
if not exist "%OUT%" mkdir "%OUT%"
call :shot r3_front "--view=front"
call :shot r3_side "--view=side"
call :shot r3_rear "--view=rear"
call :shot r3_3q "--view=three_quarter" "--finish=C_Sky_Pastel"
call :shot r3_top "--view=top"
call :shot r3_interior "--hide-shell" "--show-reserved"
call :shot r3_exploded_3q "--exploded" "--view=three_quarter"
call :shot r3_exploded_side "--exploded" "--view=side"
call :shot r3_exploded_front "--exploded" "--view=front"
call :shot r3_led_closeup "--view=led_closeup"
call :shot r3_led_blank_closeup "--view=led_closeup" "--blank-led"
call :shot r3_finish_A_3q "--view=three_quarter" "--finish=A_Pearl_Graphite"
call :shot r3_finish_B_3q "--view=three_quarter" "--finish=B_Warm_Sand"
set "MISSING=0"
for %%N in (r3_front r3_side r3_rear r3_3q r3_top r3_interior r3_exploded_3q r3_exploded_side r3_exploded_front r3_led_closeup r3_led_blank_closeup r3_finish_A_3q r3_finish_B_3q) do (
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
