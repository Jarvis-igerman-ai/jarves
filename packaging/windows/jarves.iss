; JARVES Windows installer (Inno Setup 7.1). Built by packaging/build_windows.ps1:
;   ISCC /DAppVersion=0.1.0.57 /DStage=<dist\stage> /DOutDir=<dist> /DCommit=<sha> jarves.iss
; Per user, no admin: {autopf} = %LOCALAPPDATA%\Programs. User data stays in %LOCALAPPDATA%\jarves (or JARVES_HOME)
; and survives upgrade and uninstall; only "remove my data" (asked, default No; silent: /PURGE=1) deletes it, and
; only a real JARVES data folder (purge_guard.iss: never a drive root / the profile; config.toml|store.db|memory.db).
; Not signed (no code-signing certificate yet): Windows SmartScreen shows "Windows protected your PC" ->
; "More info" -> "Run anyway". See docs/INSTALL_TEST.md.

#ifndef AppVersion
  #define AppVersion "0.1.0.0"
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
AppPublisherURL=https://github.com/igalgerman/JARVIS
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
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
UninstallDisplayName=JARVES
UninstallDisplayIcon={app}\face\JARVES Face.exe
CloseApplications=yes
RestartApplications=no
ChangesEnvironment=yes
SetupLogging=yes
UsedUserAreasWarning=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "hebrew"; MessagesFile: "compiler:Languages\Hebrew.isl"

[CustomMessages]
english.TaskPath=Add the "jarvis" command to my PATH (for jarvis key / setup / doctor)
hebrew.TaskPath=הוסף את הפקודה jarvis ל-PATH (עבור jarvis key / setup / doctor)
english.TaskAutostart=Start JARVES when I sign in to Windows
hebrew.TaskAutostart=הפעל את JARVES כשאני נכנס ל-Windows
english.RunSetup=Set up JARVES now (connection, OpenAI key)
hebrew.RunSetup=הגדר את JARVES עכשיו (חיבור, מפתח OpenAI)
english.GetPear=Get Pear Desktop for music (optional, separate app)
hebrew.GetPear=הורד את Pear Desktop למוזיקה (אופציונלי, תוכנה נפרדת)
english.AskPurge=Also delete your JARVES data (memory, settings, saved keys, logs) in:%n%1%n%nChoose No to keep it for a later reinstall.
hebrew.AskPurge=למחוק גם את הנתונים של JARVES (זיכרון, הגדרות, מפתחות שמורים, יומנים) בתיקייה:%n%1%n%nבחר "לא" כדי לשמור אותם להתקנה הבאה.

[Tasks]
Name: "path"; Description: "{cm:TaskPath}"
Name: "autostart"; Description: "{cm:TaskAutostart}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[InstallDelete]
; upgrade: replace the bundled Python and face completely (no stale packages from an older version)
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\face"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\JARVES"; Filename: "{app}\jarvis.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\face\JARVES Face.exe"
Name: "{group}\JARVES setup (connection and key)"; Filename: "{cmd}"; Parameters: "/k """"{app}\jarvis.cmd"" setup"""; WorkingDir: "{app}"
Name: "{group}\JARVES dashboard"; Filename: "{app}\jarvis.cmd"; Parameters: "dashboard"; WorkingDir: "{app}"
Name: "{group}\JARVES doctor"; Filename: "{cmd}"; Parameters: "/k """"{app}\jarvis.cmd"" doctor"""; WorkingDir: "{app}"
Name: "{group}\Check for JARVES updates"; Filename: "{cmd}"; Parameters: "/k """"{app}\jarvis.cmd"" update"""; WorkingDir: "{app}"
Name: "{group}\Uninstall JARVES"; Filename: "{uninstallexe}"
Name: "{userdesktop}\JARVES"; Filename: "{app}\jarvis.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\face\JARVES Face.exe"; Tasks: desktopicon

[Run]
; Autostart (ticked by default, owner decision) is the runtime's own mechanism: WP-M1 `jarvis autostart on|off`.
Filename: "{cmd}"; Parameters: "/c """"{app}\jarvis.cmd"" autostart on"""; Tasks: autostart; Flags: runhidden waituntilterminated
Filename: "{cmd}"; Parameters: "/k """"{app}\jarvis.cmd"" setup"""; WorkingDir: "{app}"; Description: "{cm:RunSetup}"; Flags: postinstall nowait skipifsilent
Filename: "https://github.com/pear-devs/pear-desktop/releases"; Description: "{cm:GetPear}"; Flags: postinstall shellexec nowait skipifsilent unchecked

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c """"{app}\jarvis.cmd"" stop"""; Flags: runhidden waituntilterminated; RunOnceId: "StopCore"
Filename: "{cmd}"; Parameters: "/c """"{app}\jarvis.cmd"" autostart off"""; Flags: runhidden waituntilterminated; RunOnceId: "Autostart"

[UninstallDelete]
; bytecode caches written at run time inside the program folder (never user data)
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\face"
Type: dirifempty; Name: "{app}"

[Code]
const
  EnvKey = 'Environment';

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

procedure AddToPath(const Dir: String);
var
  Paths: String;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    Paths := '';
  if PathHas(Paths, Dir) then
    exit;
  if (Paths <> '') and (Copy(Paths, Length(Paths), 1) <> ';') then
    Paths := Paths + ';';
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths + Dir);
end;

procedure RemoveFromPath(const Dir: String);
var
  Paths: String;
  P: Integer;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    exit;
  Paths := ';' + Paths + ';';
  P := Pos(';' + Lowercase(Dir) + ';', Lowercase(Paths));
  if P = 0 then
    exit;
  Delete(Paths, P, Length(Dir) + 1);
  Paths := Copy(Paths, 2, Length(Paths) - 2);
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Old: String;
begin
  // Upgrade: stop the running Core first (its python.exe locks files in {app}\python).
  Old := ExpandConstant('{app}\jarvis.cmd');
  if FileExists(Old) then
    Exec(ExpandConstant('{cmd}'), '/c ""' + Old + '" stop"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('path') then
    AddToPath(ExpandConstant('{app}'));
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
