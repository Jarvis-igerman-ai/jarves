"""#1592: a check command executes task code as the owner. On Linux it runs in a namespace where the whole file system
is read-only except its worktree and private temp folders, also for a process it leaves behind; an agent runs the
check only through a `jarves-check` script that does the same. No network; throw-away local repositories."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jarves.projectmgr import confine, gitops, runners

# every route the reviews listed: shared config, hooks, info/attributes, a new main `commondir`, other worktrees'
# metadata and `.git` pointers, this worktree's pointer, renaming the repository or the integration folder and making
# a new one there (R1), files in the owner's home that run code later, a socket folder (R2), and a detached survivor
# that reports what happened to its own late write (R3)
ATTACK = r"""
import os, subprocess, sys
common, integ, own, sock = sys.argv[1:5]
def put(p, text):
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a") as f:
            f.write(text)
        print("WROTE", p)
    except OSError as e:
        print("denied", p, e.errno)
def move(a, b):
    try:
        os.rename(a, b)
        print("WROTE moved", a)
        put(a + "/.git/config" if a.endswith("repo") else a + "/.git", "x")
    except OSError as e:
        print("denied", a, e.errno)
put(common + "/config", '[filter "x"]\n\tclean = touch PWNED\n')
put(common + "/hooks/post-merge", "#!/bin/sh\ntouch PWNED\n")
put(common + "/info/attributes", "* filter=x\n")
put(common + "/commondir", "/tmp/evil\n")
for g in os.listdir(common + "/worktrees"):
    put(common + "/worktrees/" + g + "/gitdir", "x")
    put(common + "/worktrees/" + g + "/commondir", "x")
put(integ + "/.git", "gitdir: /tmp/evil/.git\n")
put(own, "gitdir: /tmp/evil/.git\n")
move(os.path.dirname(common), os.path.dirname(common) + "-moved")
move(integ, integ + "-moved")
put(os.path.expanduser("~/.jarves-1592-probe"), "x")
print("SOCKET VISIBLE" if os.path.exists(sock + "/default") else "socket hidden")
late = '''
import os, sys, time
time.sleep(1)
try:
    open(sys.argv[1] + "/config", "a").write("[core]\\n\\tfsmonitor = touch PWNED\\n")
    out = "wrote"
except OSError as e:
    out = "denied %d" % e.errno
open("survivor.txt", "w").write(out)
'''
subprocess.Popen([sys.executable, "-c", late, common], start_new_session=True)
"""

# R4: a nested user + mount namespace must not undo the read-only mounts
NESTED = r"""
import ctypes, os, struct, sys
common = sys.argv[1]
uid, gid = os.getuid(), os.getgid()
os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNS)
for n, t in (("setgroups", "deny"), ("uid_map", f"{uid} {uid} 1"), ("gid_map", f"{gid} {gid} 1")):
    open(f"/proc/self/{n}", "w").write(t)
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
def r(name, rc):
    print(name, "ESCAPED" if rc == 0 else "denied", ctypes.get_errno())
r("umount", libc.umount2(common.encode(), 2))
r("umount-root", libc.umount2(b"/", 2))
os.makedirs("bindroot", exist_ok=True)
r("bind", libc.mount(b"/", b"bindroot", None, 4096, None))
attr = struct.pack("QQQQ", 0, 1, 0, 0)
r("clear-readonly", libc.syscall(442, -100, b"/", 0x8000, attr, len(attr)))
try:
    open(common + "/config", "a").write("[filter \"y\"]\n")
    print("config ESCAPED")
except OSError as e:
    print("config denied", e.errno)
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    """The repository, its integration and one task worktree. pytest's tmp folder is under /tmp, which a confined run
    sees as an empty tmpfs; a stand-in socket folder is hidden instead, so the real paths are what is attacked."""
    sock = tmp_path / "sock"
    sock.mkdir()
    (sock / "default").write_text("owner's server\n")
    monkeypatch.setattr(confine, "HIDE", (str(sock),))
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    home = tmp_path / "home"
    integ = gitops.ensure_integration(home, str(repo), 1)
    wt, _br = gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    yield repo, integ, wt, sock
    Path(os.path.expanduser("~/.jarves-1592-probe")).unlink(missing_ok=True)


def _state(repo: Path, integ: Path, wt: Path, sock: Path) -> dict:
    files = [p for p in (repo / ".git").rglob("*") if p.is_file() and "objects" not in p.parts]
    return {str(p): p.read_bytes() for p in [*files, integ / ".git", wt / ".git", sock / "default"]}


def _survivor(wt: Path) -> str:
    """What the detached survivor reported about its late write; bounded wait, polling its own report file."""
    f = wt / "survivor.txt"
    for _ in range(200):
        if f.exists() and f.read_text():
            return f.read_text()
        time.sleep(0.05)
    return "no report"


def _attack(repo: Path, integ: Path, wt: Path, sock: Path) -> list[str]:
    return [sys.executable, "-c", ATTACK, str(repo / ".git"), str(integ), str(wt / ".git"), str(sock)]


@pytest.mark.linux_only
@pytest.mark.userns
def test_a_check_cannot_change_any_shared_git_file_even_from_a_survivor(project):
    repo, integ, wt, sock = project
    before = _state(repo, integ, wt, sock)
    ok, out = gitops.run_check(wt, _attack(repo, integ, wt, sock))
    assert ok and "denied" in out and "WROTE" not in out and "socket hidden" in out, out
    assert _survivor(wt) == "denied 30"                         # EROFS, reported by the survivor itself
    assert _state(repo, integ, wt, sock) == before
    assert not Path(os.path.expanduser("~/.jarves-1592-probe")).exists()
    assert gitops.git(integ, "config", "--get", "filter.x.clean", check=False).stdout == ""
    assert gitops.run_check(wt, [sys.executable, "-c", "open('built.txt','w').write('ok')"])[0]
    assert (wt / "built.txt").read_text() == "ok"               # the worktree itself stays writable for the check


@pytest.mark.linux_only
@pytest.mark.userns
def test_a_nested_namespace_cannot_undo_the_read_only_mounts(project):
    repo, integ, wt, sock = project
    before = _state(repo, integ, wt, sock)
    ok, out = gitops.run_check(wt, [sys.executable, "-c", NESTED, str(repo / ".git")])
    assert ok and "ESCAPED" not in out and out.count("denied") == 5, out
    assert _state(repo, integ, wt, sock) == before


@pytest.mark.linux_only
@pytest.mark.userns
def test_a_check_has_no_network_but_a_loopback(project):
    repo, integ, wt, sock = project
    code = ("import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(); "
            "print(sorted(n for _, n in socket.if_nameindex()))")
    ok, out = gitops.run_check(wt, [sys.executable, "-c", code])
    assert ok and out == "['lo']", out


class _Script(runners.Runner):
    """A stand-in agent: it runs `jarves-check` (the only check it is allowed) from its PATH, like Claude's Bash tool."""
    name = "fake"

    def __init__(self, code: str) -> None:
        super().__init__()
        self.code, self.seen = code, {}

    def build(self, prompt, cwd, **kw) -> runners.Spec:
        self.seen.update(prompt=prompt, check_argv=kw.get("check_argv"))
        return runners.Spec([sys.executable, "-c", self.code], None, gitops.child_env())

    def parse(self, stdout: str, rc: int) -> runners.Result:
        return runners.Result(ok=rc == 0, text=stdout)


@pytest.mark.linux_only
@pytest.mark.userns
def test_an_agent_runs_the_check_only_through_the_confined_jarves_check_script(project):
    repo, integ, wt, sock = project
    before = _state(repo, integ, wt, sock)
    agent = _Script("import subprocess, sys; sys.stdout.write(subprocess.run(['jarves-check'], capture_output=True, "
                    "text=True).stdout); open('agent.txt', 'w').write('mine')")
    r = agent.run("p", str(wt), check_argv=_attack(repo, integ, wt, sock), timeout=60)
    assert r.ok and "denied" in r.text and "WROTE" not in r.text, (r.error, r.text)
    assert agent.seen["check_argv"] == ["jarves-check"] and "`jarves-check`" in agent.seen["prompt"]
    assert _survivor(wt) == "denied 30" and _state(repo, integ, wt, sock) == before
    assert (wt / "agent.txt").read_text() == "mine"             # the agent itself is not confined
    assert not list(Path(runners.tempfile.gettempdir()).glob("jarves-check-*"))  # the script folder is removed


@pytest.mark.linux_only
def test_a_read_only_agent_gets_no_check_script(project):
    repo, integ, wt, sock = project
    agent = _Script("print(1)")
    agent.run("p", str(wt), check_argv=["python"], read_only=True, timeout=30)
    assert agent.seen["check_argv"] == ["python"] and "jarves-check" not in agent.seen["prompt"]


@pytest.mark.linux_only
@pytest.mark.userns
def test_the_release_command_can_still_tag_but_not_change_the_config(project):
    repo, integ, wt, sock = project
    tag = ("import subprocess; subprocess.run(['git', 'tag', 'v1'], check=True); "
           "open(subprocess.check_output(['git', 'rev-parse', '--git-common-dir'], text=True).strip() + '/config', 'a')")
    ok, out = gitops.run_check(integ, [sys.executable, "-c", tag], release=True)
    assert not ok and "Read-only file system" in out
    assert gitops.git(repo, "tag", "--list").stdout.strip() == "v1"


@pytest.mark.linux_only
def test_a_check_that_cannot_be_confined_is_never_run_unconfined(monkeypatch, project):
    repo, integ, wt, sock = project
    monkeypatch.setattr(gitops, "_CONFINE", {"ok": True})

    def broken(*a, **k):
        raise OSError("unreadable gitdir file")
    monkeypatch.setattr(confine, "spec", broken)
    ok, out = gitops.run_check(wt, [sys.executable, "-c", "open('ran.txt', 'w')"])
    assert (ok, out) == (False, "check could not be confined: unreadable gitdir file") and not (wt / "ran.txt").exists()
    r = _Script("open('ran.txt', 'w')").run("p", str(wt), check_argv=["python"], timeout=30)
    assert "unreadable gitdir file" in r.error and not (wt / "ran.txt").exists()


@pytest.mark.linux_only
def test_a_confinement_that_cannot_be_set_up_never_runs_the_command(tmp_path):
    marker = tmp_path / "ran"
    bad = {"rw": [str(tmp_path / "missing")], "ro": [], "mask": [], "net": False}
    p = subprocess.run(confine.wrap([sys.executable, "-c", f"open({str(marker)!r},'w')"], bad),
                       capture_output=True, text=True, timeout=30, check=False)
    assert p.returncode == confine.FAIL and "could not confine" in p.stderr and not marker.exists()


def test_where_confinement_is_unavailable_the_check_runs_as_before_and_says_so_once(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(gitops, "_CONFINE", {})
    monkeypatch.setattr(gitops.sys, "platform", "darwin")
    with caplog.at_level(logging.WARNING, logger=gitops.__name__):
        assert gitops.confined(["echo"], tmp_path) == ["echo"]
        assert gitops.confined(["echo"], tmp_path) == ["echo"]
    assert [r.getMessage() for r in caplog.records].count(
        "projectmgr: check runs are not confined, the shared git files stay writable (#1592): "
        "not Linux (tracked in #1665)") == 1


@pytest.mark.linux_only
def test_the_spec_keeps_only_the_worktree_writable_unless_it_is_a_release(project):
    repo, integ, wt, sock = project
    common = (repo / ".git").resolve()
    own = common / "worktrees" / "t1-a1"
    s = confine.spec(common, own, wt)
    assert s["rw"] == [str(wt.resolve())] and s["net"] is False and s["mask"][0] == str(sock)
    assert s["ro"] == [str(common), str(wt.resolve() / ".git")]
    r = confine.spec(common, own, wt, git_writes=True, net=True)
    assert str(own) in r["rw"] and str(common / "refs") in r["rw"] and str(own / "gitdir") in r["ro"] and r["net"]


def test_the_module_imports_where_there_is_no_fcntl():
    """CI review: gitops imports confine on Windows too; a top-level POSIX-only import crashed the Core there."""
    code = ("import sys; sys.modules['fcntl'] = None; "
            "import jarves.projectmgr.gitops, jarves.projectmgr.confine; print('ok')")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False)
    assert p.stdout.strip() == "ok", p.stderr[-500:]
