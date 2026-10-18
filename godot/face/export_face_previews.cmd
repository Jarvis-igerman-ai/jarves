@echo off
rem Export the face previews (every state + every status-dot colour) into godot\previews\face\.
rem Exit 1 if any PNG is missing or empty. Set GODOT_EXE to override the winget-installed Godot 4.7.x.
setlocal
if "%GODOT_EXE%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine.GodotEngine_*") do set "GODOT_EXE=%%D\Godot_v4.7.2-stable_win64.exe"
set "PRJ=%~dp0.."
set "OUT=%PRJ%\previews\face"
if not exist "%OUT%" mkdir "%OUT%"
set "LIST="
call :shot face_idle "--state=idle" "--link=ready"
call :shot face_listening "--state=listening" "--link=live" "--level=0.55"
call :shot face_thinking "--state=thinking" "--link=live"
call :shot face_speaking "--state=speaking" "--link=live" "--level=0.7"
call :shot face_happy "--state=happy" "--link=live"
call :shot face_error "--state=error" "--link=error"
call :shot face_offline "--state=idle" "--link=offline"
call :shot dot_ready "--state=idle" "--link=ready"
call :shot dot_live "--state=idle" "--link=live"
call :shot dot_connecting "--state=idle" "--link=connecting"
call :shot dot_degraded "--state=idle" "--link=degraded"
call :shot dot_muted "--state=idle" "--link=muted"
call :shot face_idle_low "--state=idle" "--link=ready" "--low"
rem transient reactions (face.emotion) and the activity pill (assistant.activity, Hebrew sample words)
for %%E in (laugh cry surprised love sad) do call :shot react_%%E "--state=idle" "--link=live" "--emotion=%%E"
for %%K in (thinking searching reading time memory tool) do call :shot info_%%K "--state=thinking" "--link=live" "--activity=%%K"
call :shot info_music "--state=speaking" "--link=live" "--activity=music"
rem motion contact sheets on a simulated clock: 8 frames over 12 s (calm / --lively) and one blink
call :seq motion_idle_calm "--seed=4"
call :seq motion_idle_lively "--seed=4" "--lively"
call :seq motion_blink_calm "--seed=4" "--seq-blink"
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
start "" /wait "%GODOT_EXE%" --path "%PRJ%" --resolution 720x720 res://face/face_app.tscn -- --kiosk --shot="%OUT%\%~1.png" %~2 %~3 %~4 %~5
exit /b 0

:seq
set "LIST=%LIST% %~1"
if exist "%OUT%\%~1.png" del "%OUT%\%~1.png"
start "" /wait "%GODOT_EXE%" --path "%PRJ%" --resolution 720x720 res://face/face_app.tscn -- --kiosk --state=idle --link=ready --seq="%OUT%\%~1.png" %~2 %~3
exit /b 0
