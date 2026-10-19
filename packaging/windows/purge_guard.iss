// JARVES uninstaller: the "remove my data" guard, included into jarves.iss [Code] (and into the test harness that
// core/tests/test_packaging.py compiles). JARVES_HOME can point anywhere (a typo, "C:\", the user profile), and a
// silent /PURGE=1 deletes without asking, so DelTree runs only on a folder that is clearly JARVES's own data:
// - never a drive or share root, the user profile, a known user/system folder, or any PARENT of one;
// - and only when a JARVES marker file is inside (config.toml, store.db or memory.db).

function PurgeSameOrParentOf(const Dir, Other: String): Boolean;
begin
  Result := (Other <> '') and (Pos(Lowercase(AddBackslash(Dir)), Lowercase(AddBackslash(Other))) = 1);
end;

function PurgeConst(const C: String): String;
begin
  try
    Result := ExpandConstant(C);
  except
    Result := '';
  end;
end;

function IsDataDirSafeToPurge(const Raw: String): Boolean;
var
  Dir: String;
  Guarded: TArrayOfString;
  I: Integer;
begin
  Result := False;
  if Trim(Raw) = '' then
    exit;
  Dir := RemoveBackslashUnlessRoot(ExpandFileName(Trim(Raw)));
  if (Length(Dir) < 4) or (CompareText(Dir, RemoveBackslashUnlessRoot(ExtractFileDrive(Dir))) = 0) then
    exit;                                   // "C:\", "C:", "\\server\share"
  SetArrayLength(Guarded, 13);
  Guarded[0] := GetEnv('USERPROFILE');
  Guarded[1] := PurgeConst('{%USERPROFILE}');
  Guarded[2] := PurgeConst('{localappdata}');
  Guarded[3] := PurgeConst('{userappdata}');
  Guarded[4] := PurgeConst('{userdocs}');
  Guarded[5] := PurgeConst('{userdesktop}');
  Guarded[6] := PurgeConst('{win}');
  Guarded[7] := PurgeConst('{sys}');
  Guarded[8] := PurgeConst('{commonpf}');
  Guarded[9] := PurgeConst('{autopf}');
  Guarded[10] := GetEnv('TEMP');
  Guarded[11] := GetEnv('PUBLIC');
  Guarded[12] := GetEnv('ProgramData');
  for I := 0 to GetArrayLength(Guarded) - 1 do
    if PurgeSameOrParentOf(Dir, RemoveBackslashUnlessRoot(Guarded[I])) then
      exit;
  Dir := AddBackslash(Dir);
  Result := FileExists(Dir + 'config.toml') or FileExists(Dir + 'store.db') or FileExists(Dir + 'memory.db');
end;
