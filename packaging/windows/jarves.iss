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
AppPublisherURL=https://github.com/igalgerman/jarves
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
ChangesEnvironment=yes
SetupLogging=yes
UsePreviousTasks=no
ShowLanguageDialog=yes
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
; In-app update / rollback (setup started with /RESTARTJARVES=1 by jarves.updates): start the Core again, windowless.
Filename: "{app}\python\pythonw.exe"; Parameters: "-m jarves run --background"; WorkingDir: "{app}"; Check: RestartRequested; Flags: nowait runhidden
; The finish-page button below opens the dashboard without a console.
; Silent installs: JARVES-Setup.exe /VERYSILENT /SETUPANSWERS=C:\path\answers.json applies a setup answers file (no
; secrets in it; see docs/INSTALL_TEST.md). Without /SETUPANSWERS= the command does nothing (exit 0).
Filename: "{cmd}"; Parameters: "/c """"{app}\jarvis.cmd"" setup --answers ""{param:SETUPANSWERS|}"""""; WorkingDir: "{app}"; Check: WizardSilent; Flags: runhidden waituntilterminated
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
  SnapshotMade, SetupComplete: Boolean;

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

procedure MoveProgramPart(const Name: String);
var Source, Target: String;
begin
  Source := ExpandConstant('{app}\') + Name;
  Target := ExpandConstant('{app}\rollback\') + Name;
  if (FileExists(Source) or DirExists(Source)) and not RenameFile(Source, Target) then
    RaiseException('Cannot retain previous program: ' + Name);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Old, OldPython: String;
begin
  Result := '';
  if (Pos(Lowercase(AddBackslash(ExpandFileName(DataDir()))), Lowercase(AddBackslash(ExpandConstant('{app}')))) = 1) or
     (Pos(Lowercase(AddBackslash(ExpandConstant('{app}'))), Lowercase(AddBackslash(ExpandFileName(DataDir())))) = 1) then begin
    Result := 'The program and data folders must be separate.';
    exit;
  end;
  Old := ExpandConstant('{app}\jarvis.cmd');
  if FileExists(Old) then
    Exec(ExpandConstant('{cmd}'), '/c ""' + Old + '" stop"', '', SW_HIDE, ewWaitUntilTerminated, Code);
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
    if DirExists(ExpandConstant('{app}\rollback')) then
      DelTree(ExpandConstant('{app}\rollback'), True, True, True);
    ForceDirectories(ExpandConstant('{app}\rollback'));
    SnapshotMade := True;
    MoveProgramPart('python');
    MoveProgramPart('face');
    MoveProgramPart('bridge');
    MoveProgramPart('jarvis.cmd');
  end;
end;

procedure RestoreProgramPart(const Name: String);
var Source, Target: String;
begin
  Source := ExpandConstant('{app}\rollback\') + Name;
  Target := ExpandConstant('{app}\') + Name;
  if DirExists(Source) or FileExists(Source) then begin
    if DirExists(Target) then DelTree(Target, True, True, True)
    else if FileExists(Target) then DeleteFile(Target);
    if not RenameFile(Source, Target) then Log('Previous program retained at ' + Source);
  end;
end;

procedure DeinitializeSetup();
begin
  if SnapshotMade and not SetupComplete then begin
    RestoreProgramPart('python');
    RestoreProgramPart('face');
    RestoreProgramPart('bridge');
    RestoreProgramPart('jarvis.cmd');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Args, State: String;
begin
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
