@echo off
rem Export the LEGO-style assembly instruction images into docs\assembly\img\ (step_01..07, overview_labeled, parts\<pid>_<Name>).
rem Set GODOT_EXE (windowed) / GODOT_CON (console) to override the winget-installed Godot 4.7.x.
setlocal
if "%GODOT_EXE%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine.GodotEngine_*") do set "GODOT_EXE=%%D\Godot_v4.7.2-stable_win64.exe"
if "%GODOT_CON%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine.GodotEngine_*") do set "GODOT_CON=%%D\Godot_v4.7.2-stable_win64_console.exe"
for %%I in ("%~dp0..\docs\assembly\img") do set "OUT=%%~fI"
set "LST=%TEMP%\jarves_partlist.txt"
if not exist "%OUT%" mkdir "%OUT%"
if not exist "%OUT%\parts" mkdir "%OUT%\parts"
set "RES=--resolution 1280x900"

set "EXPECT=%TEMP%\jarves_expected.txt"
if exist "%EXPECT%" del "%EXPECT%"

for %%S in (01 02 03 04 05 06 07) do (
  call :shot "%OUT%\step_%%S.png" "--step=%%S"
)
call :shot "%OUT%\overview_labeled.png" "--overview"

"%GODOT_CON%" --headless --path "%~dp0." --quit-after 3000 -- "--list-parts=printed,purchased" > "%LST%" 2>&1
for /f "tokens=2,3" %%A in ('findstr /b "PARTLIST" "%LST%"') do (
  call :shot "%OUT%\parts\%%A_%%B.png" "--part=%%B"
)

set "MISSING=0"
set "COUNT=0"
for /f "usebackq delims=" %%F in ("%EXPECT%") do (
  if exist "%%F" (
    for %%G in ("%%F") do if %%~zG GTR 0 (echo OK      %%~nxG %%~zG bytes & set /a COUNT+=1) else (echo EMPTY   %%F & set "MISSING=1")
  ) else (echo MISSING %%F & set "MISSING=1")
)
echo %COUNT% images written to %OUT%
if "%COUNT%"=="0" set "MISSING=1"
if "%MISSING%"=="1" exit /b 1
exit /b 0

:shot
rem %1 = output png (quoted), %2 = mode flag (quoted)
if exist %1 del %1
echo %~1>> "%EXPECT%"
start "" /wait "%GODOT_EXE%" --path "%~dp0." %RES% -- "--shot=%~1" %2
exit /b 0
