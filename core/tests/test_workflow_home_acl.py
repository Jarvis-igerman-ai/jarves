"""JAR-260 / #824: on real Windows, access.token and sessions.json carry no inherited ACEs and no other principal.

test_workflow_824_acl.py pins the icacls argv with a mock; this reads the actual ACL with Get-Acl after the
files are created. The control test proves the reader does see inheritance on an ordinary file, so a pass
is not a reader that always says "clean". Windows-only (deselected, not skipped, elsewhere).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from jarves.workflow import server
from jarves.workflow.winproc import hidden_popen_kwargs

_PS = ("$me=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name; \"ME|$me\"; "
       "(Get-Acl -LiteralPath $env:JAR260_PATH).Access | "
       "ForEach-Object { \"ACE|$($_.IdentityReference)|$($_.IsInherited)\" }")


def _acl(path: Path) -> tuple[str, list[tuple[str, bool]]]:
    """(current user, [(identity, inherited)]) for one file, via a fixed PowerShell argv (no shell)."""
    env = dict(os.environ, JAR260_PATH=str(path))
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS],
                       capture_output=True, text=True, timeout=60, check=True, env=env, **hidden_popen_kwargs())
    me, aces = "", []
    for line in r.stdout.splitlines():
        parts = line.strip().split("|")
        if parts[0] == "ME":
            me = parts[1]
        elif parts[0] == "ACE" and len(parts) == 3:
            aces.append((parts[1], parts[2].lower() == "true"))
    assert me and aces, r.stdout + r.stderr
    return me, aces


def _sid_name(sid: str) -> str:
    """Account name for a well-known SID, lower case, as Get-Acl prints it (names are localised)."""
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                        "(New-Object System.Security.Principal.SecurityIdentifier $env:JAR260_SID)"
                        ".Translate([System.Security.Principal.NTAccount]).Value"],
                       capture_output=True, text=True, timeout=60, check=True,
                       env=dict(os.environ, JAR260_SID=sid), **hidden_popen_kwargs())
    name = r.stdout.strip().lower()
    assert name, r.stderr
    return name


def _assert_owner_only(path: Path) -> None:
    me, aces = _acl(path)
    assert [a for a in aces if a[1]] == [], f"{path.name} still has inherited ACEs: {aces}"
    others = [ident for ident, _ in aces if ident.lower() != me.lower()]
    assert others == [], f"{path.name} grants other principals: {others}"


@pytest.mark.windows_only
def test_reader_sees_inherited_aces_on_an_ordinary_file(tmp_path):
    plain = tmp_path / "plain.txt"
    plain.write_text("x", encoding="ascii")
    _, aces = _acl(plain)
    assert any(inherited for _, inherited in aces), aces


@pytest.mark.windows_only
def test_access_token_and_sessions_are_owner_only_after_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_TIGHTENED", set())
    home = tmp_path / "workflow"
    server.access_token(home)
    server.save_sessions(home, {})
    _assert_owner_only(home / "access.token")
    _assert_owner_only(home / "sessions.json")


@pytest.mark.windows_only
def test_existing_sessions_file_is_owner_only_after_startup_load(tmp_path, monkeypatch):
    """JAR-260 reopened: a sessions.json written before the fix (inherited ACL) is tightened on load."""
    monkeypatch.setattr(server, "_TIGHTENED", set())
    home = tmp_path / "workflow"
    home.mkdir()
    # Build the pre-fix state explicitly (no skip): the folder hands an inheritable read ACE for the
    # built-in Users group (SID S-1-5-32-545) down to the file, as an old home folder did before #824.
    subprocess.run(["icacls", str(home), "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"],
                   capture_output=True, text=True, timeout=60, check=True, **hidden_popen_kwargs())
    old = home / "sessions.json"
    old.write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    _, before = _acl(old)
    users = _sid_name("S-1-5-32-545")  # localised name of BUILTIN\Users (#1778 review R3)
    assert (users, True) in [(i.lower(), inh) for i, inh in before], (users, before)
    server.load_sessions(home)
    _assert_owner_only(old)
