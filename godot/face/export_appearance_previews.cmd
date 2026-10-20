@echo off
rem Export the appearance previews (face / 2D avatar / 3D avatar / hidden, same states) into godot\previews\appearance\.
rem The window opens OFF-SCREEN with the dummy audio driver (silent; Godot 4.7 --headless cannot render pixels).
rem Exit 1 if any PNG is missing or empty. Set GODOT_EXE to override the winget-installed Godot 4.7.x.
setlocal
if "%GODOT_EXE%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine.GodotEngine_*") do set "GODOT_EXE=%%D\Godot_v4.7.2-stable_win64.exe"
set "PRJ=%~dp0.."
set "OUT=%PRJ%\previews\appearance"
if not exist "%OUT%" mkdir "%OUT%"
set "LIST="
for %%M in (face avatar2d avatar3d) do (
  call :shot %%M_idle "--appearance=%%M" "--state=idle" "--link=ready"
  call :shot %%M_listening "--appearance=%%M" "--state=listening" "--link=live" "--level=0.55"
  call :shot %%M_thinking "--appearance=%%M" "--state=thinking" "--link=live"
  call :shot %%M_speaking "--appearance=%%M" "--state=speaking" "--link=live" "--level=0.7"
  call :shot %%M_happy "--appearance=%%M" "--state=happy" "--link=live"
  call :shot %%M_error "--appearance=%%M" "--state=error" "--link=error"
  call :shot %%M_offline "--appearance=%%M" "--state=idle" "--link=offline"
  call :shot %%M_laugh "--appearance=%%M" "--state=idle" "--link=live" "--emotion=laugh"
  call :shot %%M_searching "--appearance=%%M" "--state=thinking" "--link=live" "--activity=searching"
  call :shot %%M_cry "--appearance=%%M" "--state=idle" "--link=live" "--emotion=cry"
  call :shot %%M_sad "--appearance=%%M" "--state=idle" "--link=live" "--emotion=sad"
  call :shot %%M_surprised "--appearance=%%M" "--state=idle" "--link=live" "--emotion=surprised"
  call :shot %%M_love "--appearance=%%M" "--state=idle" "--link=live" "--emotion=love"
)
call :shot hidden "--appearance=hidden" "--state=idle" "--link=ready"
set "MISSING=0"
for %%N in (%LIST%) do (
  if exist "%OUT%\%%N.png" (
    for %%F in ("%OUT%\%%N.png") do if %%~zF GTR 0 (echo OK      %%N.png %%~zF bytes) else (echo EMPTY   %%N.png & set "MISSING=1")
  ) else (echo MISSING %%N.png & set "MISSING=1")
)
if "%MISSING%"=="1" exit /b 1
exit /b 0

:shot
set "LIST=%LIST% %~1"
if exist "%OUT%\%~1.png" del "%OUT%\%~1.png"
start "" /wait "%GODOT_EXE%" --path "%PRJ%" --audio-driver Dummy --position -4000,-4000 --resolution 720x720 res://face/face_app.tscn -- --kiosk --seed=4 --shot="%OUT%\%~1.png" %~2 %~3 %~4 %~5
exit /b 0
