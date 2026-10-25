; JARVES Windows installer (Inno Setup 7.1). Built by packaging/build_windows.ps1:
;   ISCC /DAppVersion=0.1.0.57 /DStage=<dist\stage> /DOutDir=<dist> /DCommit=<sha> jarves.iss
; Per user, no admin: {autopf} = %LOCALAPPDATA%\Programs. User data stays in %LOCALAPPDATA%\jarves (or JARVES_HOME)
; and survives upgrade and uninstall; only "remove my data" (asked, default No; silent: /PURGE=1) deletes it, and
; only a real JARVES data folder (purge_guard.iss: never a drive root / the profile; config.toml|store.db|memory.db).
; SW-V4 L: "Start JARVES when I sign in" is OFF by default (the runtime's `jarvis autostart on` is the only writer of the
; Run key; silent upgrades preserve it and interactive choices take effect). Setup refuses to
; install OVER A NEWER VERSION unless /ROLLBACK=1 is given (jarvis update --rollback), so a stale installer cannot
; downgrade by accident. The AppId never changes: every released series (0.1.0.x -> 3.x -> 4.x) is the same app.
; Not signed (no code-signing certificate yet; owner decision 20 Oct 2026: stay unsigned for now): Windows SmartScreen shows "Windows protected your PC" ->
; "More info" -> "Run anyway". See docs/INSTALL_TEST.md.

#ifndef AppVersion
  #define AppVersion "0.1.0.0"
#endif
#ifndef BuildCompression
  #define BuildCompression "lzma2/max"
#endif
#ifndef Commit
  #define Commit "unknown"
#endif

[Setup]
AppId={{24F7B79A-973C-462D-BA26-896D8EA30913}
AppName=JARVES
AppVersion={#AppVersion}
AppVerName=JARVES {#AppVersion}
AppPublisher=JARVES project
AppPublisherURL=https://github.com/Jarvis-igerman-ai/jarves
AppComments=Commit {#Commit}
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={autopf}\JARVES
DefaultGroupName=JARVES
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=JARVES-Setup-{#AppVersion}
Compression={#BuildCompression}
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
UninstallDisplayName=JARVES
UninstallDisplayIcon={app}\face\JARVES Face.exe
CloseApplications=yes
RestartApplications=no
; Two setups at once (an auto-update and a manual "Update now", a double click) would race on the rollback folder and
; on the data backup/migration: the second one waits/refuses while the first holds this mutex.
SetupMutex=JARVESSetupMutex_24F7B79A
ChangesEnvironment=yes
SetupLogging=yes
UsePreviousTasks=no
ShowLanguageDialog=yes
LanguageDetectionMethod=none
UsedUserAreasWarning=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "hebrew"; MessagesFile: "compiler:Languages\Hebrew.isl"

[CustomMessages]
english.TaskPath=Add the "jarvis" command to my PATH (for jarvis key / setup / doctor)
hebrew.TaskPath=הוסף את הפקודה jarvis ל-PATH (עבור jarvis key / setup / doctor)
english.TaskAutostart=Start JARVES when I sign in to Windows
hebrew.TaskAutostart=הפעל את JARVES כשאני נכנס ל-Windows
english.RunSetup=Set up JARVES now
hebrew.RunSetup=הגדר את JARVES עכשיו
english.ShowNotes=Show what is new in this version
hebrew.ShowNotes=הצג מה חדש בגרסה הזו
english.GetPear=Get Pear Desktop for music (optional, separate app)
hebrew.GetPear=הורד את Pear Desktop למוזיקה (אופציונלי, תוכנה נפרדת)
english.AskPurge=Also delete your JARVES data (memory, settings, saved keys, logs) in:%n%1%n%nChoose No to keep it for a later reinstall.
hebrew.AskPurge=למחוק גם את הנתונים של JARVES (זיכרון, הגדרות, מפתחות שמורים, יומנים) בתיקייה:%n%1%n%nבחר "לא" כדי לשמור אותם להתקנה הבאה.

english.ExistingInstall=Existing installation found
hebrew.ExistingInstall=נמצאה התקנה קיימת
english.ExistingData=Existing data folder found
hebrew.ExistingData=נמצאה תיקיית נתונים קיימת
english.NewInstall=New installation
hebrew.NewInstall=התקנה חדשה
english.KeepTitle=Existing settings and data
hebrew.KeepTitle=הגדרות ונתונים קיימים
english.KeepDescription=Choose how to handle an earlier version
hebrew.KeepDescription=בחר כיצד לטפל בגרסה קודמת
english.KeepData=Keep my settings and data from the old version
hebrew.KeepData=שמור את ההגדרות והנתונים שלי מהגרסה הישנה
english.KeepHelp=Setup checks the existing installation and data folder. It verifies a backup before migration and retains the previous program for rollback. Clearing this box moves the old data aside; it is not deleted.
hebrew.KeepHelp=ההתקנה בודקת התקנה ותיקיית נתונים קיימות. גיבוי מאומת נוצר לפני הסבה והגרסה הקודמת נשמרת לחזרה. ביטול הסימון מעביר את הנתונים הישנים לתיקייה נפרדת ואינו מוחק אותם.
english.RetainFailed=JARVES could not be updated because a file in the program folder is in use (%1). Nothing was changed: the current version and your data are as before. Close programs that may use JARVES files (an open folder or editor, a backup or antivirus scan) and run the update again.
hebrew.RetainFailed=לא ניתן היה לעדכן את JARVES כי קובץ בתיקיית התוכנה בשימוש (%1). שום דבר לא השתנה: הגרסה הנוכחית והנתונים שלך כמו קודם. סגור תוכנות שעשויות להשתמש בקבצים של JARVES (תיקייה או עורך פתוחים, גיבוי או סריקת אנטי-וירוס) והפעל את העדכון שוב.
english.ProgramIncomplete=Setup stopped: the previous JARVES program could not be put back completely (%1 is still in %2). Nothing else was changed and your data is safe. Restart Windows and run the update again; setup puts the previous program back first.
hebrew.ProgramIncomplete=ההתקנה נעצרה: לא ניתן היה להחזיר את התוכנה הקודמת של JARVES במלואה (%1 עדיין נמצא ב-%2). שום דבר אחר לא השתנה והנתונים שלך בטוחים. הפעל מחדש את Windows והפעל את העדכון שוב; ההתקנה תחזיר קודם את התוכנה הקודמת.
english.LowDisk=Not enough free disk space on %1. The backup and the new version need about %2 MB; %3 MB more is needed. Nothing was changed: the current version and your data are as before. Free some space and run the update again.
hebrew.LowDisk=אין מספיק מקום פנוי בכונן %1. הגיבוי והגרסה החדשה צריכים בערך %2 MB; חסרים עוד %3 MB. שום דבר לא השתנה: הגרסה הנוכחית והנתונים שלך כמו קודם. פנה מקום והפעל את העדכון שוב.
english.BackupFailed=The backup or migration failed. Your old data was kept. Check free disk space and retry. See the installer log for the failed stage.
hebrew.BackupFailed=הגיבוי או ההסבה נכשלו. הנתונים הישנים נשמרו. בדוק מקום פנוי ונסה שוב. השלב שנכשל מופיע ביומן ההתקנה.

[Tasks]
Name: "autostart"; Description: "{cm:TaskAutostart}"; Flags: unchecked
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[InstallDelete]
Type: files; Name: "{userdesktop}\JARVES.lnk"; Check: RemoveDesktopShortcut
; upgrade: replace the bundled Python and face completely (no stale packages from an older version)
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\face"

[Files]
Source: "..\installer_state.py"; Flags: dontcopy
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\JARVES"; Filename: "{app}\jarvis.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\face\JARVES Face.exe"
Name: "{group}\JARVES setup"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I ""{app}\installer_state.py"" open-setup --home ""{code:GetDataDir}"""; WorkingDir: "{app}"
Name: "{group}\JARVES dashboard"; Filename: "{app}\jarvis.cmd"; Parameters: "dashboard"; WorkingDir: "{app}"
Name: "{group}\JARVES doctor"; Filename: "{cmd}"; Parameters: "/k """"{app}\jarvis.cmd"" doctor"""; WorkingDir: "{app}"
Name: "{group}\Check for JARVES updates"; Filename: "{cmd}"; Parameters: "/k """"{app}\jarvis.cmd"" update"""; WorkingDir: "{app}"
Name: "{group}\Uninstall JARVES"; Filename: "{uninstallexe}"
Name: "{userdesktop}\JARVES"; Filename: "{app}\jarvis.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\face\JARVES Face.exe"; Tasks: desktopicon

[Run]
; Autostart is OFF by default; use the app's own mechanism when explicitly selected.
Filename: "{cmd}"; Parameters: "/c """"{app}\jarvis.cmd"" autostart on"""; Tasks: autostart; Flags: runhidden waituntilterminated
Filename: "{cmd}"; Parameters: "/c """"{app}\jarvis.cmd"" autostart off"""; Check: DisableAutostart; Flags: runhidden waituntilterminated
; In-app update / rollback (setup started with /RESTARTJARVES=1 by jarves.updates): start the Core again, windowless,
; only when it was running before (#166; unknown = it was), with its window when the window was open.
Filename: "{app}\python\pythonw.exe"; Parameters: "{code:CoreRunArgs}"; WorkingDir: "{app}"; Check: RestartCoreNow; Flags: nowait runhidden
; #166: a dashboard that was open is opened again with the same safe flags (never one that was not running).
Filename: "{app}\python\pythonw.exe"; Parameters: "{code:DashboardRunArgs}"; WorkingDir: "{app}"; Check: RestartDashboardNow; Flags: nowait runhidden
; The finish-page button below opens the dashboard without a console.
; Silent installs: JARVES-Setup.exe /VERYSILENT /SETUPANSWERS=C:\path\answers.json applies a setup answers file (no
; secrets in it; see docs/INSTALL_TEST.md). Without /SETUPANSWERS= the command does nothing (exit 0).
Filename: "{cmd}"; Parameters: "/c """"{app}\jarvis.cmd"" setup --answers ""{param:SETUPANSWERS|}"""""; WorkingDir: "{app}"; Check: WizardSilent; Flags: runhidden waituntilterminated
; Release notes: opens an HTML page (local file, written by `jarvis release-notes --open`); on by default, never in silent installs.
Filename: "{app}\python\pythonw.exe"; Parameters: "-m jarves release-notes --open"; WorkingDir: "{app}"; Description: "{cm:ShowNotes}"; Flags: postinstall nowait skipifsilent
Filename: "https://github.com/pear-devs/pear-desktop/releases"; Description: "{cm:GetPear}"; Flags: postinstall shellexec nowait skipifsilent unchecked

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: "-I ""{app}\installer_state.py"" restore-shims --home ""{code:GetDataDir}"" --state ""{app}\install-state.json"""; Flags: runhidden waituntilterminated; RunOnceId: "RestoreShims"
Filename: "{cmd}"; Parameters: "/c """"{app}\jarvis.cmd"" stop"""; Flags: runhidden waituntilterminated; RunOnceId: "StopCore"
Filename: "{cmd}"; Parameters: "/c """"{app}\jarvis.cmd"" autostart off"""; Flags: runhidden waituntilterminated; RunOnceId: "Autostart"

[UninstallDelete]
; bytecode caches written at run time inside the program folder (never user data)
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\face"
Type: filesandordirs; Name: "{app}\rollback"
Type: filesandordirs; Name: "{app}\failed-upgrade-*"
Type: files; Name: "{app}\install-state.json"
Type: dirifempty; Name: "{app}"

[Code]
const
  EnvKey = 'Environment';
  InstallerKey = 'Software\JARVES\Installer';
var
  KeepPage: TInputOptionWizardPage;
  Detection: TNewStaticText;
  SetupButton: TNewButton;
  BackupState: String;
  SnapshotMade, SetupComplete, CoreStopped: Boolean;
  Retained: String;                                  // '|python|face|': the parts THIS run moved into rollback
  WasRecorded, WasCore, WasFace, WasDash: Boolean;   // what ran before the update (#166)
  DashArgs: String;

function DataDir(): String;
begin
  Result := GetEnv('JARVES_HOME');
  if Result = '' then
    Result := ExpandConstant('{localappdata}\jarves');
end;

function PathHas(const Paths, Dir: String): Boolean;
begin
  Result := Pos(';' + Lowercase(Dir) + ';', ';' + Lowercase(Paths) + ';') > 0;
end;

function WithoutPath(const Paths, Dir: String): String;
var
  Rest, Part: String;
  P: Integer;
begin
  Result := '';
  Rest := Paths;
  while Rest <> '' do begin
    P := Pos(';', Rest);
    if P = 0 then begin Part := Rest; Rest := ''; end
    else begin Part := Copy(Rest, 1, P - 1); Delete(Rest, 1, P); end;
    if CompareText(RemoveBackslashUnlessRoot(Trim(Part)), RemoveBackslashUnlessRoot(Dir)) <> 0 then begin
      if Result <> '' then Result := Result + ';';
      Result := Result + Part;
    end;
  end;
end;

procedure AddToPath(const Dir: String);
var
  Paths: String;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then Paths := '';
  if not RegValueExists(HKCU, InstallerKey, 'PathBefore') then
    RegWriteStringValue(HKCU, InstallerKey, 'PathBefore', Paths);
  Paths := WithoutPath(Paths, Dir);
  if Paths <> '' then Paths := ';' + Paths;
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Dir + Paths);
end;

procedure RemoveFromPath(const Dir: String);
var
  Paths, Before: String;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then exit;
  Paths := WithoutPath(Paths, Dir);
  if RegQueryStringValue(HKCU, InstallerKey, 'PathBefore', Before) then begin
    if CompareText(Paths, WithoutPath(Before, Dir)) = 0 then Paths := Before;
    RegDeleteValue(HKCU, InstallerKey, 'PathBefore');
    RegDeleteKeyIfEmpty(HKCU, InstallerKey);
  end;
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths);
end;

function ParamPresent(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;

function RemoveDesktopShortcut(): Boolean;
begin
  Result := not WizardSilent() and not WizardIsTaskSelected('desktopicon');
end;

function DisableAutostart(): Boolean;
begin
  Result := not WizardSilent() and not WizardIsTaskSelected('autostart');
end;

function RestartRequested(): Boolean;
begin
  Result := ParamPresent('/RESTARTJARVES=1');
end;

#include "downgrade_guard.iss"

function InitializeSetup(): Boolean;
var
  Installed: String;
begin
  Result := True;
  if not RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{24F7B79A-973C-462D-BA26-896D8EA30913}_is1',
                             'DisplayVersion', Installed) then
    exit;                                   // not installed yet
  if IsDowngrade(Installed, '{#AppVersion}') and not ParamPresent('/ROLLBACK=1') then
  begin
    Log('Refusing to install {#AppVersion} over the newer ' + Installed + ' (use /ROLLBACK=1)');
    if not WizardSilent() then
      MsgBox('JARVES ' + Installed + ' is already installed. This older installer will not replace it.' + #13#10 +
             'To go back deliberately use: jarvis update --rollback', mbError, MB_OK);
    Result := False;
  end;
end;

function GetDataDir(Param: String): String;
begin
  Result := DataDir();
end;

function KeepData(): Boolean;
begin
  Result := True;
  if not WizardSilent() then Result := KeepPage.Values[0];
end;

procedure OpenSetup(Sender: TObject);
var Code: Integer;
begin
  if WizardSilent() then exit;
  if not Exec(ExpandConstant('{app}\python\pythonw.exe'),
    '-I "' + ExpandConstant('{app}\installer_state.py') + '" open-setup --home "' + DataDir() + '"',
    ExpandConstant('{app}'), SW_HIDE, ewNoWait, Code) then
    MsgBox(CustomMessage('BackupFailed'), mbError, MB_OK);
end;

procedure InitializeWizard();
begin
  KeepPage := CreateInputOptionPage(wpSelectDir, CustomMessage('KeepTitle'),
    CustomMessage('KeepDescription'), CustomMessage('KeepHelp'), False, False);
  KeepPage.Add(CustomMessage('KeepData'));
  KeepPage.Values[0] := True;
  KeepPage.CheckListBox.Height := ScaleY(40);
  Detection := TNewStaticText.Create(KeepPage);
  Detection.Parent := KeepPage.Surface;
  Detection.Top := KeepPage.CheckListBox.Top + KeepPage.CheckListBox.Height + ScaleY(8);
  Detection.Width := KeepPage.SurfaceWidth;
  Detection.Height := ScaleY(48);
  Detection.WordWrap := True;
  SetupButton := TNewButton.Create(WizardForm);
  SetupButton.Parent := WizardForm.FinishedPage;
  SetupButton.Caption := CustomMessage('RunSetup');
  SetupButton.Left := WizardForm.FinishedLabel.Left;
  SetupButton.Top := WizardForm.FinishedPage.Height - ScaleY(60);
  SetupButton.Width := ScaleX(220);
  SetupButton.Height := ScaleY(28);
  SetupButton.OnClick := @OpenSetup;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = KeepPage.ID then begin
    Detection.Caption := CustomMessage('NewInstall');
    if FileExists(ExpandConstant('{app}\jarvis.cmd')) then
      Detection.Caption := CustomMessage('ExistingInstall');
    Detection.Caption := Detection.Caption + ': ' + ExpandConstant('{app}');
    if DirExists(DataDir()) then
      Detection.Caption := Detection.Caption + #13#10 + CustomMessage('ExistingData') + ': ' + DataDir();
  end;
end;

// A Pascal string as a PowerShell single-quoted literal (PowerShell also ends a literal at the typographic quotes).
function PSQuote(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '''', '''''', True);
  StringChangeEx(Result, #$2018, #$2018#$2018, True);
  StringChangeEx(Result, #$2019, #$2019#$2019, True);
  StringChangeEx(Result, #$201A, #$201A#$201A, True);
  StringChangeEx(Result, #$201B, #$201B#$201B, True);
  Result := '''' + Result + '''';
end;

function Utf16LeByte(const S: String; I: Integer): Integer;
begin
  if I mod 2 = 0 then Result := Ord(S[I div 2 + 1]) and 255
  else Result := (Ord(S[I div 2 + 1]) shr 8) and 255;
end;

// powershell.exe -EncodedCommand takes the script as base64 of its UTF-16LE text. Execution policy (also one set by
// Group Policy: AllSigned / Restricted) applies to script FILES only, never to -EncodedCommand, so the stop below
// always runs (with -File a locked-down machine stopped nothing and every upgrade was refused).
function EncodedCommand(const S: String): String;
var
  N, I, B0, B1, B2: Integer;
  Alphabet: String;
begin
  Alphabet := 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  Result := '';
  N := 2 * Length(S);
  I := 0;
  while I < N do begin
    B0 := Utf16LeByte(S, I);
    B1 := 0;
    B2 := 0;
    if I + 1 < N then B1 := Utf16LeByte(S, I + 1);
    if I + 2 < N then B2 := Utf16LeByte(S, I + 2);
    Result := Result + Alphabet[(B0 shr 2) + 1] + Alphabet[(((B0 and 3) shl 4) or (B1 shr 4)) + 1];
    if I + 1 < N then Result := Result + Alphabet[(((B1 and 15) shl 2) or (B2 shr 6)) + 1]
    else Result := Result + '=';
    if I + 2 < N then Result := Result + Alphabet[(B2 and 63) + 1]
    else Result := Result + '=';
    I := I + 3;
  end;
end;

// A running python.exe/face/bridge from {app} locks the folder and the rename fails (issue #141): the dashboard or
// any extra process is not stopped by `jarvis stop`. First (WaitS > 0, right after `jarvis stop`) the script waits up
// to WaitS seconds until every Core lock (<data>\core.lock, <data>\profiles\*\core.lock) is free, so the Core closes
// its databases by itself instead of being hard-killed. Then every process whose executable lies under {app}\python,
// \face, \bridge or \rollback (matched by path, never by command line; never the whole {app}, which a first install
// may have put in a shared folder) is first asked to close (windowed ones: face, music player), given a short grace,
// and only then hard-stopped. Each step is written to the setup log by name (#153).
function StopScript(const Dir, LogFile, LockRoot: String; WaitS: Integer; const WasFile: String; DetectOnly: Boolean): String;
var Detect: String;
begin
  Detect := '$false';
  if DetectOnly then Detect := '$true';
  Result :=
    '$Dir = ' + PSQuote(Dir) + #13#10 +
    '$Log = ' + PSQuote(LogFile) + #13#10 +
    '$LockRoot = ' + PSQuote(LockRoot) + #13#10 +
    '$WaitS = ' + IntToStr(WaitS) + #13#10 +
    '$Was = ' + PSQuote(WasFile) + #13#10 +
    '$DetectOnly = ' + Detect + #13#10 +
    '$GraceS = 8' + #13#10 +
    '$d = $Dir.TrimEnd([char]92) + [char]92' + #13#10 +
    '$out = @()' + #13#10 +
    'if ($d.Length -le 3) { "refused: the program folder is a drive root" | Set-Content -LiteralPath $Log -Encoding ASCII; exit 0 }' + #13#10 +
    'function Held($f) { $s = $null; try { $s = [IO.File]::Open($f, "Open", "ReadWrite", "ReadWrite") } catch { return $false }' + #13#10 +
    '  try { $s.Lock(0, 1); $s.Unlock(0, 1); return $false } catch { return $true } finally { $s.Dispose() } }' + #13#10 +
    'if ($WaitS -gt 0 -and $LockRoot) {' + #13#10 +
    '  $locks = @(Join-Path $LockRoot "core.lock")' + #13#10 +
    '  $pd = Join-Path $LockRoot "profiles"' + #13#10 +
    '  if (Test-Path -LiteralPath $pd) { $locks += @(Get-ChildItem -LiteralPath $pd -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName "core.lock" }) }' + #13#10 +
    '  $locks = @($locks | Where-Object { Test-Path -LiteralPath $_ })' + #13#10 +
    '  $sw = [Diagnostics.Stopwatch]::StartNew()' + #13#10 +
    '  while (@($locks | Where-Object { Held $_ }).Count -and $sw.Elapsed.TotalSeconds -lt $WaitS) { Start-Sleep -Milliseconds 250 }' + #13#10 +
    '  $still = @($locks | Where-Object { Held $_ })' + #13#10 +
    '  if ($still.Count) { $out += "the Core did not close within $WaitS s (still locked: $($still -join ", ")); it is stopped below" }' + #13#10 +
    '  elseif ($locks.Count) { $out += "the Core has closed (lock free after $([math]::Round($sw.Elapsed.TotalSeconds, 1)) s)" }' + #13#10 +
    '}' + #13#10 +
    '$parts = @(@("python", "face", "bridge", "rollback") | ForEach-Object { $d + $_ + [char]92 })' + #13#10 +
    '$p = @(Get-Process | Where-Object { $q = $_.Path; $q -and @($parts | Where-Object { $q.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) }).Count })' + #13#10 +
    // #166: what runs now: the Core by its lock holder pid, the window by a {app}\face process, the dashboard by its
    // own marker (pid must be a program process; only the four safe flags are kept). Never by command line.
    'if ($Was) {' + #13#10 +
    '  $ran = @()' + #13#10 +
    '  try {' + #13#10 +
    '    $fs = [IO.File]::Open((Join-Path $LockRoot "core.lock"), "Open", "Read", "ReadWrite")' + #13#10 +
    '    try { [void]$fs.Seek(1, "Begin"); $b = New-Object byte[] 4096; $n = $fs.Read($b, 0, 4096); $cp = [int]([Text.Encoding]::UTF8.GetString($b, 0, $n).Trim([char]0, [char]32, [char]10, [char]13) | ConvertFrom-Json).pid } finally { $fs.Dispose() }' + #13#10 +
    '    if ($cp -and @($p | Where-Object { $_.Id -eq $cp }).Count) { $ran += "core" }' + #13#10 +
    '  } catch {}' + #13#10 +
    '  if (@($p | Where-Object { $_.Path.StartsWith($d + "face" + [char]92, [StringComparison]::OrdinalIgnoreCase) }).Count) { $ran += "face" }' + #13#10 +
    '  try {' + #13#10 +
    '    $m = Get-Content -LiteralPath (Join-Path $LockRoot "dashboard.run.json") -Raw -ErrorAction Stop | ConvertFrom-Json' + #13#10 +
    '    if (@($p | Where-Object { $_.Id -eq [int]$m.pid }).Count) {' + #13#10 +
    '      $f = @($m.flags | Where-Object { $_ -in "--lan", "--mobile", "--tls-selfsigned", "--tailnet-only" })' + #13#10 +
    '      $ran += ("dashboard " + ($f -join " ")).Trim()' + #13#10 +
    '    }' + #13#10 +
    '  } catch {}' + #13#10 +
    '  [IO.File]::WriteAllText($Was, ($ran -join [Environment]::NewLine))' + #13#10 +
    '  if ($DetectOnly) { exit 0 }' + #13#10 +
    '}' + #13#10 +
    '$asked = @($p | Where-Object { $_.MainWindowHandle -ne 0 })' + #13#10 +
    'foreach ($x in $asked) { try { [void]$x.CloseMainWindow(); $out += "asked to close: $($x.ProcessName) (pid $($x.Id))" } catch {} }' + #13#10 +
    'function Alive($list) { @($list | Where-Object { try { -not $_.HasExited } catch { $false } }) }' + #13#10 +
    '$end = [DateTime]::UtcNow.AddSeconds($GraceS)' + #13#10 +
    'while ($asked.Count -and (Alive $asked).Count -and [DateTime]::UtcNow -lt $end) { Start-Sleep -Milliseconds 250 }' + #13#10 +
    '$end = [DateTime]::UtcNow.AddSeconds(2)' + #13#10 +
    'while ($p.Count -and (Alive $p).Count -and [DateTime]::UtcNow -lt $end) { Start-Sleep -Milliseconds 250 }' + #13#10 +
    'foreach ($x in $p) {' + #13#10 +
    '  if ((Alive @($x)).Count) { $out += "hard-stopped: $($x.ProcessName) (pid $($x.Id))"; Stop-Process -Id $x.Id -Force -ErrorAction SilentlyContinue }' + #13#10 +
    '  else { $out += "ended by itself: $($x.ProcessName) (pid $($x.Id))" }' + #13#10 +
    '}' + #13#10 +
    'foreach ($x in $p) { try { [void]$x.WaitForExit(5000) } catch {} }' + #13#10 +
    'if (-not $p.Count) { $out += "no process was running from the program folder (looked under $d; elevated: $(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(544)))"' + #13#10 +
    '  foreach ($x in @(Get-Process -Name python, pythonw, "JARVES Face" -ErrorAction SilentlyContinue)) { $out += "  seen elsewhere: $($x.ProcessName) (pid $($x.Id)) $($x.Path)" } }' + #13#10 +
    '$out | Set-Content -LiteralPath $Log -Encoding ASCII' + #13#10;
end;

function GetLongPathNameW(ShortPath: String; LongPath: String; Size: Cardinal): Cardinal;
  external 'GetLongPathNameW@kernel32.dll stdcall';

// {app} can arrive as an 8.3 short path (/DIR=C:\Users\RUNNER~1\...); a process path is always the long one, so match
// against the long spelling or nothing is ever found.
function LongPath(const Path: String): String;
var
  N: Cardinal;
  Buf: String;
begin
  Result := Path;
  Buf := StringOfChar(' ', 1024);
  N := GetLongPathNameW(Path, Buf, 1024);
  if (N > 0) and (N < 1024) then Result := Copy(Buf, 1, N);
end;

procedure StopProcessesUnderApp(const WaitS: Integer);
var
  Code, I: Integer;
  Dir, LogFile: String;
  Lines: TArrayOfString;
begin
  Dir := RemoveBackslashUnlessRoot(LongPath(ExpandConstant('{app}')));
  if Length(Dir) <= 3 then exit;                     // never sweep a whole drive
  LogFile := ExpandConstant('{tmp}\jarves-stop-app.log');
  DeleteFile(LogFile);
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' +
    EncodedCommand(StopScript(Dir, LogFile, DataDir(), WaitS, '', False)), '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Log('Stopping JARVES before the upgrade: PowerShell could not be started (' + SysErrorMessage(Code) + ')');
  if LoadStringsFromFile(LogFile, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
      Log('Stopping JARVES before the upgrade: ' + Lines[I]);
end;

// #293: the in-app path (`jarvis update install`) stops the Core itself before setup starts, so the detection below sees
// nothing. It passes what ran as /WASRUNNING=core,face,dashboard:lan:mobile (or "none"); that is OR-ed into the
// detection. Dashboard flags are taken only from the four safe ones.
procedure ApplyWasRunningParam();
var
  P, Tok, Flag, Args: String;
  K, J: Integer;
begin
  P := ExpandConstant('{param:WASRUNNING|}');
  if P = '' then exit;
  WasRecorded := True;
  P := P + ',';
  while P <> '' do begin
    K := Pos(',', P);
    Tok := Copy(P, 1, K - 1);
    Delete(P, 1, K);
    if Tok = 'core' then WasCore := True
    else if Tok = 'face' then WasFace := True
    else if Pos('dashboard', Tok) = 1 then begin
      WasDash := True;
      Args := '';
      Tok := Tok + ':';
      Delete(Tok, 1, 10);                              // up to and including "dashboard:"
      while Tok <> '' do begin
        J := Pos(':', Tok);
        Flag := Copy(Tok, 1, J - 1);
        Delete(Tok, 1, J);
        if (Flag = 'lan') or (Flag = 'mobile') or (Flag = 'tls-selfsigned') or (Flag = 'tailnet-only') then
          Args := Args + ' --' + Flag;
      end;
      DashArgs := Trim(Args);
    end;
  end;
  Log('Told by the caller what ran before the update (/WASRUNNING=): ' + ExpandConstant('{param:WASRUNNING|}'));
end;

// #166: what was running before setup stopped it (the Core, its window, a standalone dashboard) is what is started
// again. Detected BEFORE anything is stopped (the same encoded script, detect-only). Unknown (no old program, the
// check failed) counts as "the Core was running": the behaviour before this change.
procedure RecordWhatRuns();
var
  Code, I: Integer;
  Dir, LogFile, WasFile, Line: String;
  Lines: TArrayOfString;
begin
  Dir := RemoveBackslashUnlessRoot(LongPath(ExpandConstant('{app}')));
  if Length(Dir) <= 3 then exit;
  LogFile := ExpandConstant('{tmp}\jarves-detect-app.log');
  WasFile := ExpandConstant('{tmp}\jarves-was-running.txt');
  DeleteFile(WasFile);
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' +
    EncodedCommand(StopScript(Dir, LogFile, DataDir(), 0, WasFile, True)), '', SW_HIDE, ewWaitUntilTerminated, Code) or
    (Code <> 0) or not FileExists(WasFile) then begin
    Log('Could not tell what was running before the update; the Core is restarted if setup was asked to');
    ApplyWasRunningParam();
    exit;
  end;
  WasRecorded := True;
  LoadStringsFromFile(WasFile, Lines);
  for I := 0 to GetArrayLength(Lines) - 1 do begin
    Line := Trim(Lines[I]);
    if Line = 'core' then WasCore := True
    else if Line = 'face' then WasFace := True
    else if Pos('dashboard', Line) = 1 then begin WasDash := True; DashArgs := Trim(Copy(Line, 10, 200)); end;
    if Line <> '' then Log('Running before the update: ' + Line);
  end;
  ApplyWasRunningParam();
  if not (WasCore or WasFace or WasDash) then Log('Running before the update: nothing');
end;

function CoreWasRunning(): Boolean;
begin
  // only the detection decides; unknown = the Core was running. (`cmd /c jarvis.cmd stop` exits 0 even when no Core
  // runs, so its exit code is no witness: CI showed a Core restarted after nothing ran.)
  Result := WasCore or not WasRecorded;
end;

function CoreRunArgs(Param: String): String;
begin
  Result := '-m jarves run --background';
  if WasFace then Result := Result + ' --show';
end;

function DashboardRunArgs(Param: String): String;
begin
  Result := '-m jarves dashboard ' + DashArgs;
end;

function RestartCoreNow(): Boolean;
begin
  Result := RestartRequested() and CoreWasRunning();
end;

function RestartDashboardNow(): Boolean;
begin
  Result := RestartRequested() and WasRecorded and WasDash;
end;

// Start again exactly what was running (setup ended early, or the health check undid / kept the update).
procedure StartWhatRan();
var Code: Integer;
begin
  if not RestartRequested() or not FileExists(ExpandConstant('{app}\python\pythonw.exe')) then exit;
  if CoreWasRunning() then
    Exec(ExpandConstant('{app}\python\pythonw.exe'), CoreRunArgs(''), ExpandConstant('{app}'), SW_HIDE, ewNoWait, Code);
  if WasRecorded and WasDash then
    Exec(ExpandConstant('{app}\python\pythonw.exe'), DashboardRunArgs(''), ExpandConstant('{app}'), SW_HIDE, ewNoWait, Code);
end;

// Keep the previous program for rollback: rename {app}\<Name> to {app}\rollback\<Name> (same volume, no copy).
function RetainProgramPart(const Name: String): Boolean;
var
  Source, Target: String;
  Attempt: Integer;
begin
  Source := ExpandConstant('{app}\') + Name;
  Target := ExpandConstant('{app}\rollback\') + Name;
  Result := True;
  if not (FileExists(Source) or DirExists(Source)) then exit;
  Result := False;
  // retry: antivirus / indexer / a just-stopped process can hold a handle for a moment
  for Attempt := 1 to 10 do begin
    Result := RenameFile(Source, Target);
    if Result then begin
      Retained := Retained + Name + '|';
      break;
    end;
    Log('Retaining previous program ' + Name + ' failed (attempt ' + IntToStr(Attempt) + '), retrying');
    StopProcessesUnderApp(0);
    Sleep(1000);
  end;
  if not Result then
    Log('Cannot retain previous program: ' + Name + ' (a file in it stays in use)');
end;

// A free {app}\failed-upgrade-<Tag>-<time>-<Name> path (removed by the uninstaller).
function AsideName(const Tag, Name: String): String;
var I: Integer;
begin
  Result := ExpandConstant('{app}\failed-upgrade-') + Tag + '-' + GetDateTimeString('yyyymmddhhnnss', #0, #0) + '-' + Name;
  I := 1;
  while (DirExists(Result) or FileExists(Result)) and (I < 100) do begin
    Result := ExpandConstant('{app}\failed-upgrade-') + Tag + '-' + GetDateTimeString('yyyymmddhhnnss', #0, #0) + '-' +
              IntToStr(I) + '-' + Name;
    I := I + 1;
  end;
end;

function PartsPending(): Boolean;
begin
  Result := (Retained <> '') and (Retained <> '|');
end;

// Put back one part THIS run moved (a stale leftover from an older run never replaces the working program). Nothing is
// deleted: whatever is at the target (a half-installed new part) is first renamed aside to failed-upgrade-*, and put
// back again if the old part cannot go in, so the program folder never has a hole. A part that is back is removed
// from Retained; one that is not stays there (and in the restore-pending marker) for the next try.
function RestoreProgramPart(const Name: String): Boolean;
var Source, Target, Aside: String;
begin
  Result := True;
  if Pos('|' + Name + '|', Retained) = 0 then exit;
  Source := ExpandConstant('{app}\rollback\') + Name;
  Target := ExpandConstant('{app}\') + Name;
  if not (DirExists(Source) or FileExists(Source)) then begin
    Log('Previous program part ' + Name + ' is no longer in the rollback folder; nothing to put back');
    StringChangeEx(Retained, '|' + Name + '|', '|', True);
    exit;
  end;
  Aside := '';
  if DirExists(Target) or FileExists(Target) then begin
    Aside := AsideName('restore', Name);
    if not RenameFile(Target, Aside) then begin
      Log('Previous program part ' + Name + ' kept at ' + Source + ': the part at ' + Target + ' could not be moved aside');
      Result := False;
      exit;
    end;
    Log('Moved ' + Target + ' aside to ' + Aside + ' to put the previous program back');
  end;
  if RenameFile(Source, Target) then
    StringChangeEx(Retained, '|' + Name + '|', '|', True)
  else begin
    Log('Previous program part ' + Name + ' kept at ' + Source + ': it could not be put back');
    if (Aside <> '') and not RenameFile(Aside, Target) then
      Log('The part moved aside stays at ' + Aside);
    Result := False;
  end;
end;

function PendingMarker(): String;
begin
  Result := ExpandConstant('{app}\rollback\restore-pending.txt');
end;

// Parts that could not be put back are written to rollback\restore-pending.txt: the next setup puts them back FIRST
// (before its data backup and before it clears the rollback folder), so they are never deleted.
procedure RestoreProgram();
begin
  RestoreProgramPart('python');
  RestoreProgramPart('face');
  RestoreProgramPart('bridge');
  RestoreProgramPart('jarvis.cmd');
  if PartsPending() then begin
    Log('Not every part of the previous program could be put back; still in the rollback folder: ' + Retained);
    SaveStringToFile(PendingMarker(), Retained, False);
  end else begin
    DeleteFile(PendingMarker());
    Retained := '|';
  end;
end;

// The first pending part, for the message.
function FirstPending(): String;
begin
  Result := Copy(Retained, 2, Length(Retained));
  if Pos('|', Result) > 0 then Result := Copy(Result, 1, Pos('|', Result) - 1);
end;

// An earlier setup could not put the previous program back (restore-pending.txt), or an older installer left
// {app}\python missing while rollback\python is there (it deleted before it renamed). Put those parts back now:
// otherwise the old python is missing and the verified data backup below would be skipped. False = still missing.
function RecoverPendingRestore(): Boolean;
var Pending: AnsiString;
begin
  Retained := '|';
  if LoadStringFromFile(PendingMarker(), Pending) then Retained := Trim(String(Pending));
  if (Retained = '') or (Copy(Retained, 1, 1) <> '|') then Retained := '|';
  if FileExists(ExpandConstant('{app}\jarvis.cmd')) and not FileExists(ExpandConstant('{app}\python\python.exe')) and
     FileExists(ExpandConstant('{app}\rollback\python\python.exe')) and (Pos('|python|', Retained) = 0) then
    Retained := Retained + 'python|';
  Result := True;
  if not PartsPending() then exit;
  Log('Putting back the previous program first (left by an earlier setup): ' + Retained);
  RestoreProgram();
  Result := not PartsPending();
end;

// A rollback folder left by an older run is replaced. If it cannot be deleted (a read-only or locked file), it is moved
// aside to failed-upgrade-* (removed by the uninstaller) so it does not block this upgrade (#153). False = it is still
// there: the upgrade stops before anything is moved.
function ClearOldRollback(): Boolean;
var Old, Aside: String;
begin
  Old := ExpandConstant('{app}\rollback');
  Result := True;
  if not DirExists(Old) then exit;
  DelTree(Old, True, True, True);
  if DirExists(Old) then begin
    Aside := ExpandConstant('{app}\failed-upgrade-rollback-') + GetDateTimeString('yyyymmddhhnnss', #0, #0);
    if RenameFile(Old, Aside) then Log('Old rollback folder could not be deleted; moved aside to ' + Aside)
    else Log('Old rollback folder could not be deleted or moved: ' + Old);
  end;
  Result := not DirExists(Old);
end;

// #165: free-space check on the data and program volumes BEFORE anything else (before JARVES is stopped, before the
// backup), so a nearly full disk is refused up front with the amount needed and the running Core is not bounced.
// Exit code 4 = too little; an unreadable probe (or a bad /DISKRESERVEMB=) never blocks.
function DiskKey(const Lines: TArrayOfString; const Key: String): String;
var I: Integer;
begin
  Result := '';
  for I := 0 to GetArrayLength(Lines) - 1 do
    if Pos(Key + '=', Lines[I]) = 1 then Result := Copy(Lines[I], Length(Key) + 2, 100);
end;

function LowDiskMessage(): String;
var
  Code: Integer;
  OutFile: String;
  Lines: TArrayOfString;
begin
  Result := '';
  if not FileExists(ExpandConstant('{app}\python\python.exe')) then exit;      // first install: nothing to back up
  ExtractTemporaryFile('installer_state.py');
  OutFile := ExpandConstant('{tmp}\jarves-disk.txt');
  DeleteFile(OutFile);
  if not Exec(ExpandConstant('{app}\python\python.exe'), '-I "' + ExpandConstant('{tmp}\installer_state.py') +
      '" diskcheck --home "' + DataDir() + '" --app "' + ExpandConstant('{app}') + '" --out "' + OutFile +
      '" --reserve-mb ' + IntToStr(StrToIntDef(ExpandConstant('{param:DISKRESERVEMB|0}'), 0)),
      '', SW_HIDE, ewWaitUntilTerminated, Code) then exit;
  Log('Disk space check result code: ' + IntToStr(Code));
  if (Code = 4) and LoadStringsFromFile(OutFile, Lines) then
    Result := FmtMessage(CustomMessage('LowDisk'), [DiskKey(Lines, 'drive'), DiskKey(Lines, 'need_mb'), DiskKey(Lines, 'short_mb')]);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code, WaitS: Integer;
  Old, OldPython: String;
begin
  Result := '';
  if (Pos(Lowercase(AddBackslash(ExpandFileName(DataDir()))), Lowercase(AddBackslash(ExpandConstant('{app}')))) = 1) or
     (Pos(Lowercase(AddBackslash(ExpandConstant('{app}'))), Lowercase(AddBackslash(ExpandFileName(DataDir())))) = 1) then begin
    Result := 'The program and data folders must be separate.';
    exit;
  end;
  // review P3-1: the disk check needs only the old python, so it runs before JARVES is stopped
  Result := LowDiskMessage();
  if Result <> '' then exit;
  Old := ExpandConstant('{app}\jarvis.cmd');
  WaitS := 0;
  RecordWhatRuns();                                  // #166: before anything is stopped
  if FileExists(Old) then begin
    if Exec(ExpandConstant('{cmd}'), '/c ""' + Old + '" stop"', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Log('jarvis stop exit code: ' + IntToStr(Code));
    CoreStopped := True;
    WaitS := 15;                                     // `jarvis stop` only asks: wait for the Core to let go of core.lock
  end;
  StopProcessesUnderApp(WaitS);
  if not RecoverPendingRestore() then begin
    Result := FmtMessage(CustomMessage('ProgramIncomplete'), [FirstPending(), ExpandConstant('{app}\rollback')]);
    exit;
  end;
  BackupState := ExpandConstant('{tmp}\jarves-preupgrade.json');
  OldPython := ExpandConstant('{app}\python\python.exe');
  if FileExists(OldPython) then begin
    ExtractTemporaryFile('installer_state.py');
    if not Exec(OldPython, '-I "' + ExpandConstant('{tmp}\installer_state.py') +
      '" prepare --home "' + DataDir() + '" --state "' + BackupState + '"',
      '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then begin
      Result := CustomMessage('BackupFailed');
      exit;
    end;
    // Rename within the same volume: retain a usable prior runtime without copying 16,500 files.
    if not ClearOldRollback() then begin
      Result := FmtMessage(CustomMessage('RetainFailed'), ['rollback']);
      exit;
    end;
    ForceDirectories(ExpandConstant('{app}\rollback'));
    Retained := '|';
    SnapshotMade := True;
    if not RetainProgramPart('python') then Result := FmtMessage(CustomMessage('RetainFailed'), ['python'])
    else if not RetainProgramPart('face') then Result := FmtMessage(CustomMessage('RetainFailed'), ['face'])
    else if not RetainProgramPart('bridge') then Result := FmtMessage(CustomMessage('RetainFailed'), ['bridge'])
    else if not RetainProgramPart('jarvis.cmd') then Result := FmtMessage(CustomMessage('RetainFailed'), ['jarvis.cmd']);
    if Result <> '' then begin
      // put back what was already moved, now: the old version stays complete even if the wizard is retried
      RestoreProgram();
      // a part that could not be put back keeps SnapshotMade set, so DeinitializeSetup tries again; never say
      // "nothing was changed" then
      SnapshotMade := PartsPending();
      if SnapshotMade then
        Result := FmtMessage(CustomMessage('ProgramIncomplete'), [FirstPending(), ExpandConstant('{app}\rollback')]);
    end;
  end;
end;

procedure DeinitializeSetup();
var Code: Integer;
begin
  if SnapshotMade and not SetupComplete then
    RestoreProgram();
  // The update stopped JARVES and did not finish: start the old version again, as the finished update would have.
  if CoreStopped and not SetupComplete then StartWhatRan();
end;

// Health check after an in-app update (/RESTARTJARVES=1, #164): the new Core must reach CORE_READY (recorded in
// updates\pending.json by the Core itself) within /HEALTHTIMEOUT= seconds (default 180). If it never does:
// - the data schema did not change: the PROGRAM parts this run moved are put back from {app}\rollback (the new ones are
//   kept aside in failed-upgrade-health-*, nothing is deleted) and the old Core is started;
// - the data schema changed (`complete` migrated it): the old Core would refuse the newer data, so the program is NOT
//   swapped; health.json says health_failed and the owner is offered the approval-gated `jarvis update --rollback`,
//   which restores the data backup and the program together.
// Data is never touched here. The check and every helper run with the OLD python ({app}\rollback\python, or {app}\python
// once it is back) and the stdlib-only {tmp}\installer_state.py: a broken new runtime cannot hide its own failure.
function SwapBackPart(const Name, Aside: String): Boolean;
var Source, Target, Kept: String;
begin
  Result := True;
  if Pos('|' + Name + '|', Retained) = 0 then exit;
  Source := ExpandConstant('{app}\rollback\') + Name;
  Target := ExpandConstant('{app}\') + Name;
  Kept := Aside + '\' + Name;
  if not (FileExists(Source) or DirExists(Source)) then begin Result := False; exit; end;
  if FileExists(Target) or DirExists(Target) then
    if not RenameFile(Target, Kept) then begin Result := False; exit; end;
  Result := RenameFile(Source, Target);
  if not Result then begin
    if FileExists(Kept) or DirExists(Kept) then RenameFile(Kept, Target);   // put the new part back: never leave a hole
    Log('Health rollback: cannot put back ' + Name);
  end else
    StringChangeEx(Retained, '|' + Name + '|', '|', True);
end;

// The old runtime: still in rollback\ before the swap, back in {app}\python after it.
function HealthPython(): String;
begin
  Result := ExpandConstant('{app}\rollback\python\python.exe');
  if not FileExists(Result) then Result := ExpandConstant('{app}\python\python.exe');
end;

function HelperScript(): String;
begin
  Result := ExpandConstant('{tmp}\installer_state.py');
  if not FileExists(Result) then ExtractTemporaryFile('installer_state.py');
end;

procedure NoteHealth(const Status: String);
var Code: Integer;
begin
  if not Exec(HealthPython(), '-I "' + HelperScript() + '" note-rollback --home "' + DataDir() + '" --status ' + Status,
      '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
    Log('Health check: could not record "' + Status + '" for the owner notice');
end;

// health --expect <this version> with the given timeout; /HEALTHEXPECT= exists for the smoke test only (it fakes an
// update record for a version that never starts)
function RunHealth(const TimeoutS: Integer; var Code: Integer): Boolean;
begin
  Log('Health check runs with ' + HealthPython() + ' (timeout ' + IntToStr(TimeoutS) + ' s)');
  Result := Exec(HealthPython(), '-I "' + HelperScript() + '" health --home "' + DataDir() + '" --timeout ' +
    IntToStr(TimeoutS) + ' --expect ' + ExpandConstant('{param:HEALTHEXPECT|{#AppVersion}}'), '', SW_HIDE,
    ewWaitUntilTerminated, Code);
end;

procedure HealthRollback();
var
  Code: Integer;
  Aside: String;
  Ok: Boolean;
begin
  if not Exec(HealthPython(), '-I "' + HelperScript() + '" can-program-rollback --home "' + DataDir() + '" --state "' +
      ExpandConstant('{app}\install-state.json') + '"', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then begin
    Log('Health check failed, and the data schema changed in this update (or could not be compared): the previous ' +
        'program is NOT put back (it would not start on the upgraded data). The owner is offered the approval-gated ' +
        'jarvis update --rollback, which restores data and program together.');
    NoteHealth('health_failed');
    exit;
  end;
  Log('Health check failed: the new version did not become ready; putting the previous program back (data untouched)');
  // ask the new Core to close and wait for its core.lock before anything is hard-stopped (review P2-2)
  if FileExists(ExpandConstant('{app}\jarvis.cmd')) then
    Exec(ExpandConstant('{cmd}'), '/c ""' + ExpandConstant('{app}\jarvis.cmd') + '" stop"', '', SW_HIDE,
      ewWaitUntilTerminated, Code);
  StopProcessesUnderApp(15);
  // review #260 F6: a slow but good version may have become ready while the stop ran: never swap it out then
  if RunHealth(1, Code) and (Code = 0) then begin
    Log('Health check: the new version became ready after all; it stays, and what ran is started again');
    StartWhatRan();
    exit;
  end;
  Aside := AsideName('health', 'program');
  ForceDirectories(Aside);
  Ok := SwapBackPart('python', Aside);
  if Ok then Ok := SwapBackPart('face', Aside);
  if Ok then Ok := SwapBackPart('bridge', Aside);
  if Ok then Ok := SwapBackPart('jarvis.cmd', Aside);
  if Ok then NoteHealth('rolled_back') else NoteHealth('rollback_failed');
  if Ok then begin
    Log('Health rollback done: the previous program is back');
    StartWhatRan();
  end else
    Log('Health rollback incomplete; see the lines above. Run: jarvis update --rollback');
end;

procedure HealthCheckAfterUpdate();
var Code: Integer;
begin
  if not (SetupComplete and SnapshotMade and RestartRequested()) or ParamPresent('/ROLLBACK=1') then exit;
  if not RestartCoreNow() then begin                 // #166: a Core that was not running is not started, so not judged
    Log('Health check skipped: the Core was not running before the update, so it was not started');
    exit;
  end;
  if Pos('|python|', Retained) = 0 then exit;          // no old runtime kept: nothing to judge or put back with
  // review #260 F4: a mistyped /HEALTHTIMEOUT= falls back to 180 s; it must never make a healthy update look broken
  if not RunHealth(StrToIntDef(ExpandConstant('{param:HEALTHTIMEOUT|180}'), 180), Code) then begin
    Log('Health check could not run (the previous python did not start): ' + SysErrorMessage(Code));
    exit;
  end;
  Log('Health check result code: ' + IntToStr(Code) + ' (0 ready, 1 no update record for this version, anything else not ready)');
  if (Code <> 0) and (Code <> 1) then HealthRollback();
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Args, State: String;
begin
  if CurStep = ssDone then HealthCheckAfterUpdate();
  if CurStep = ssPostInstall then begin
    State := ExpandConstant('{app}\install-state.json');
    if FileExists(BackupState) and not FileCopy(BackupState, State, False) then
      RaiseException(CustomMessage('BackupFailed'));
    Args := '-I "' + ExpandConstant('{app}\installer_state.py') + '" complete --home "' + DataDir() +
            '" --state "' + State + '" --language ';
    if ActiveLanguage() = 'hebrew' then Args := Args + 'he' else Args := Args + 'en';
    if not KeepData() then Args := Args + ' --fresh';
    if not Exec(ExpandConstant('{app}\python\python.exe'), Args, '', SW_HIDE, ewWaitUntilTerminated, Code) or
       (Code <> 0) then RaiseException(CustomMessage('BackupFailed'));
    // PATH is always installed first in the per-user PATH, including silent updater installs.
    AddToPath(ExpandConstant('{app}'));
    SetupComplete := True;
  end;
end;

#include "purge_guard.iss"

function PurgeRequested(): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/PURGE=1') = 0 then
      Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dir: String;
begin
  if CurUninstallStep = usUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
  if CurUninstallStep = usPostUninstall then
  begin
    Dir := DataDir();
    if not DirExists(Dir) then
      exit;
    if not IsDataDirSafeToPurge(Dir) then
    begin
      // never DelTree a drive root, the profile or a folder without JARVES data in it (JARVES_HOME can be anything)
      Log('JARVES data folder kept (not a JARVES data folder, or a protected folder): ' + Dir);
      exit;
    end;
    if UninstallSilent() then
    begin
      if PurgeRequested() then
        DelTree(Dir, True, True, True);
    end
    else if MsgBox(FmtMessage(CustomMessage('AskPurge'), [Dir]), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(Dir, True, True, True);
  end;
end;
