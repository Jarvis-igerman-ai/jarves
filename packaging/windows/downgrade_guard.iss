// JARVES setup: refuse to install over a NEWER version, included into jarves.iss [Code] (and into the test harness
// that core/tests/test_packaging.py compiles). Versions are 1-4 numeric parts (0.1.0.57, 3.0.0.1, 4.0.0.2); setup
// compares them as numbers, never as text ("3.0.0.1" is newer than "0.1.0.57", "0.1.0.9" is older than "0.1.0.10").

function IsDowngrade(const Installed, Candidate: String): Boolean;
var
  A, B: Int64;
begin
  Result := False;
  if StrToVersion(Installed, A) and StrToVersion(Candidate, B) then
    Result := ComparePackedVersion(A, B) > 0;
end;
