"""JAR-406: an existing explicit grant must not survive secret-file hardening."""
import json
import os
import subprocess

import pytest

from jarves.workflow import server
from jarves.workflow.winproc import hidden_popen_kwargs


@pytest.mark.windows_only
def test_explicit_other_principal_is_removed(tmp_path):
    # Include PowerShell metacharacters: the path must be data, never script.
    path = tmp_path / "secret ' [literal] $name.txt"
    path.write_text("private", encoding="ascii")
    env = {**os.environ, "JARVES_ACL_TEST_PATH": str(path)}
    env.pop("PSModulePath", None)  # do not feed PowerShell 7 modules to Windows PowerShell

    def powershell(script):
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, capture_output=True, text=True, timeout=15, check=False,
            **hidden_popen_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    # Everyone is a stable SID, independent of the runner's display language.
    powershell("$ErrorActionPreference='Stop'; $p=$env:JARVES_ACL_TEST_PATH; "
               "$a=[System.IO.File]::GetAccessControl($p); "
               "$s=[System.Security.Principal.SecurityIdentifier]::new('S-1-1-0'); "
               "$a.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($s,'Read','Allow')); "
               "[System.IO.File]::SetAccessControl($p,$a)")
    inspect = ("$ErrorActionPreference='Stop'; $a=[System.IO.File]::GetAccessControl($env:JARVES_ACL_TEST_PATH); "
               "$u=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
               "@{protected=$a.AreAccessRulesProtected; user=$u; "
               "rules=@($a.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]) | "
               "ForEach-Object {$_.IdentityReference.Value})} | ConvertTo-Json -Compress")
    assert 'S-1-1-0' in json.loads(powershell(inspect))["rules"]
    assert server.owner_only_file(path)
    actual = json.loads(powershell(inspect))
    assert actual["protected"] is True
    assert actual["rules"] == [actual["user"]]
    assert path.read_text(encoding="ascii") == "private"
