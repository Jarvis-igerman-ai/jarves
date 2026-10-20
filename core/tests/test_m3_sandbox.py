"""WP-M3 file sandbox: escape attempts fail safely on EVERY invocation route (direct API, voice/agent tool, scheduled
job, notes export, dashboard API); links/junctions, overwrite, executables, hard links, quota; folder grants are
pending until approved, scoped, read-only by default and revocable; system / JARVES-data folders can't be granted."""
import os
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from jarves import notes as N
from jarves import sandbox as SB
from jarves import scheduler as S
from jarves.skills import SkillRegistry
from jarves.userdata import UserDB

MALICIOUS = ["../evil.md", "..\\evil.md", "a/../../evil.md", "/etc/passwd", "C:\\Windows\\win.ini", "c:evil.md",
             "\\\\server\\share\\x.md", "//server/share/x.md", "notes.md:secret", "CON", "nul.txt", "aux.md",
             "x/LPT1.txt", "trailing.md.", "trailing.md ", "bad\x00.md", "~/x.md", "a/./../../x.md", "a\\..\\..\\x.md",
             "e" * 300 + ".md", "x<y>.md", "what?.md"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ("LOCALAPPDATA", "APPDATA"):          # pytest's tmp dir lives under %LOCALAPPDATA%\Temp on Windows
        monkeypatch.delenv(k, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    db = UserDB(home / "memory.db")
    sb = SB.Sandbox(home / "files", db, home=home)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    yield SimpleNamespace(home=home, db=db, sb=sb, outside=outside, tmp=tmp_path)
    db.close()


def make_junction(link, target) -> bool:
    if sys.platform == "win32":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        return r.returncode == 0
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


def no_escape(env) -> None:
    assert (env.outside / "secret.txt").read_text(encoding="utf-8") == "TOP SECRET"
    assert not (env.tmp / "evil.md").exists() and not (env.home / "evil.md").exists()
    assert sorted(p.name for p in env.outside.iterdir()) == ["secret.txt"]


@pytest.mark.parametrize("path", MALICIOUS)
def test_direct_api_refuses_malicious_paths(env, path):
    with pytest.raises(SB.SandboxError) as e:
        env.sb.write_text(path, "x")
    assert e.value.code in ("SANDBOX_DENIED", "SANDBOX_TYPE")
    with pytest.raises(SB.SandboxError):
        env.sb.read_text(path)
    no_escape(env)


def test_normal_files_and_guards(env):
    r = env.sb.write_text("reports/today.md", "# hi")
    assert r == {"path": "reports/today.md", "bytes": 4, "replaced": False}
    assert env.sb.read_text("reports/today.md")["text"] == "# hi"
    with pytest.raises(SB.SandboxError) as e:
        env.sb.write_text("reports/today.md", "again")
    assert e.value.code == "SANDBOX_EXISTS"
    assert env.sb.write_text("reports/today.md", "again", overwrite=True)["replaced"] is True
    for exe in ("run.exe", "x.bat", "x.ps1", "x.cmd", "x.vbs", "x.js", "x.lnk", "x.sh", "x.dll", "noext"):
        with pytest.raises(SB.SandboxError) as e:
            env.sb.write_text(exe, "payload")
        assert e.value.code == "SANDBOX_TYPE"
    p = env.sb.root / "ro.txt"
    p.write_text("keep", encoding="utf-8")
    os.chmod(p, 0o444)
    with pytest.raises(SB.SandboxError):
        env.sb.write_text("ro.txt", "changed", overwrite=True)
    os.chmod(p, 0o666)
    assert p.read_text(encoding="utf-8") == "keep"
    listing = env.sb.list("")
    assert {i["name"] for i in listing["items"]} >= {"reports", "ro.txt"}


def test_quota_and_file_size(env):
    env.db.set_setting("files.quota_mb", 1)
    env.db.set_setting("files.max_file_mb", 1)
    env.sb.write_text("a.txt", "x" * 700_000)
    with pytest.raises(SB.SandboxError) as e:
        env.sb.write_text("b.txt", "x" * 700_000)
    assert e.value.code == "SANDBOX_QUOTA"
    with pytest.raises(SB.SandboxError):
        env.sb.write_text("c.txt", "x" * 2_000_000)


def test_junction_or_symlink_is_never_followed(env):
    link = env.sb.root / "door"
    assert make_junction(link, env.outside), "could not create a real junction/symlink in the tmp dir"
    for path in ("door/secret.txt", "door/new.md", "door"):
        with pytest.raises(SB.SandboxError):
            env.sb.write_text(path, "pwned", overwrite=True)
        with pytest.raises(SB.SandboxError):
            env.sb.read_text(path)
    listing = env.sb.list("")
    assert [i for i in listing["items"] if i["name"] == "door"][0]["link"] is True
    with pytest.raises(SB.SandboxError):
        env.sb.list("door")
    no_escape(env)


def test_hard_link_is_not_overwritten(env):
    target = env.outside / "hard.txt"
    target.write_text("outside data", encoding="utf-8")
    os.link(target, env.sb.root / "hard.txt")                # a real hard link (NTFS and ext4/tmpfs both support it)
    with pytest.raises(SB.SandboxError):
        env.sb.write_text("hard.txt", "pwned", overwrite=True)
    assert target.read_text(encoding="utf-8") == "outside data"


def test_grants_pending_approve_scope_revoke(env):
    shared = env.tmp / "shared"
    shared.mkdir()
    (shared / "doc.txt").write_text("shared doc", encoding="utf-8")
    g = env.sb.request_grant(str(shared), "read", by="voice")
    assert g["status"] == "pending"
    with pytest.raises(SB.SandboxError):                    # pending = no access
        env.sb.read_text(f"grant:{g['id']}/doc.txt")
    env.sb.decide(g["id"], "approve", by="dashboard")
    assert env.sb.read_text(f"grant:{g['id']}/doc.txt")["text"] == "shared doc"
    with pytest.raises(SB.SandboxError):                    # read grant: no writes
        env.sb.write_text(f"grant:{g['id']}/new.md", "x")
    for bad in (f"grant:{g['id']}/../outside/secret.txt", f"grant:{g['id']}/..\\outside\\secret.txt",
                f"grant:{g['id'] + 99}/doc.txt"):
        with pytest.raises(SB.SandboxError):
            env.sb.read_text(bad)
    env.sb.decide(g["id"], "revoke", by="dashboard")
    with pytest.raises(SB.SandboxError):
        env.sb.read_text(f"grant:{g['id']}/doc.txt")
    rw = env.sb.add_grant(str(shared), "readwrite", by="dashboard")
    assert env.sb.write_text(f"grant:{rw['id']}/out.md", "ok")["path"] == f"grant:{rw['id']}/out.md"
    assert (shared / "out.md").read_text(encoding="utf-8") == "ok"
    actions = [a["action"] for a in env.db.audit_log()]
    assert {"grant_request", "grant_approve", "grant_revoke"} <= set(actions)


def test_forbidden_grant_folders(env, monkeypatch):
    sysdir = env.tmp / "Windows"
    sysdir.mkdir()
    monkeypatch.setenv("SystemRoot", str(sysdir))
    anchor = os.path.splitdrive(str(env.tmp))[0] + os.sep if sys.platform == "win32" else "/"
    for bad in (str(env.home), str(env.home / "files"), str(env.sb.root), str(sysdir), anchor, "relative/dir",
                str(env.tmp / "missing")):
        with pytest.raises(SB.SandboxError):
            env.sb.request_grant(bad, "read")
    link = env.tmp / "linkdir"
    if make_junction(link, env.outside):
        with pytest.raises(SB.SandboxError):
            env.sb.request_grant(str(link), "read")


# ---------------------------------------------------------------------- every invocation route
async def test_voice_and_agent_tool_route(env):
    reg = SkillRegistry(allowed_actions=SB.ALLOWED_ACTIONS)
    reg.register(SB.make_skill(env.sb))
    for path in MALICIOUS:
        r = await reg.call("file_write", {"path": path[:240] or "x", "text": "pwned"}, origin="model")
        assert not r.ok and r.code in ("SANDBOX_DENIED", "SANDBOX_TYPE", "TOOL_BAD_ARGS"), path
        r = await reg.call("file_read", {"path": path[:240]}, origin="model")
        assert not r.ok, path
    assert (await reg.call("file_write", {"path": "ok.md", "text": "fine"})).ok
    g = await reg.call("folder_grant_request", {"path": str(env.outside), "mode": "readwrite"})
    assert g.ok and g.data["status"] == "pending"
    assert not (await reg.call("file_read", {"path": f"grant:{g.data['grant_id']}/secret.txt"})).ok
    no_escape(env)


async def test_scheduled_job_route(env):
    tz = ZoneInfo("Asia/Jerusalem")
    t = [datetime(2026, 10, 19, 7, 0, tzinfo=tz).timestamp()]
    reg = SkillRegistry(allowed_actions=SB.ALLOWED_ACTIONS)
    reg.register(SB.make_skill(env.sb))
    s = S.Scheduler(env.db, registry=reg, clock=lambda: t[0])
    ids = [s.preview("כל בוקר ב-8", "", action="tool", tool="file_write",
                     tool_args={"path": p, "text": "pwned"})["id"] for p in ("../evil.md", "C:\\x.md", "a/../../e.md")]
    for i in ids:
        s.approve(i, by="dashboard")
    t[0] = datetime(2026, 10, 19, 8, 0, 5, tzinfo=tz).timestamp()
    await s.tick()
    import asyncio
    await asyncio.gather(*[x for st in s._running.values() for x in st])
    for i in ids:
        run = s.runs(i)[0]
        assert run["state"] == "failed" and run["error"].startswith("SANDBOX_DENIED")
    no_escape(env)


def test_notes_export_route(env):
    notes = N.Notes(env.db)
    n = notes.add("../../evil", "body")
    r = notes.export(env.sb, n["id"])
    assert r["path"].startswith("exports/notes/") and ".." not in r["path"]
    n2 = notes.add("CON", "device name title")
    assert notes.export(env.sb, n2["id"])["path"].startswith("exports/notes/con-")
    no_escape(env)


def test_dashboard_route(env):
    ctx = SimpleNamespace(home=env.home, cfg=None, _userdata=env.db, _sandbox=env.sb)
    for path in MALICIOUS:
        st, obj = SB.api_file_read(ctx, None, None, {"path": [path]})
        assert st in (403, 404) and "TOP SECRET" not in str(obj), path
        st, obj = SB.api_files(ctx, None, None, {"path": [path]})
        assert st in (403, 404), path
    st, obj = SB.api_grant_add(ctx, None, {"path": str(env.home), "mode": "readwrite"})
    assert st == 403
    st, obj = SB.api_grant_add(ctx, None, {"path": str(env.outside), "mode": "read"})
    assert st == 200 and obj["status"] == "active"
    st, obj = SB.api_file_read(ctx, None, None, {"path": [f"grant:{obj['id']}/secret.txt"]})
    assert st == 200 and obj["text"] == "TOP SECRET"            # the user's own approved grant works
