"""#1592: a check command executes task code as the owner. On Linux it runs in a namespace where the whole file system
is read-only except its worktree and private temp folders, also for a process it leaves behind; an agent runs the
check only through a `jarves-check` script that does the same. No network; throw-away local repositories."""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from jarves.projectmgr import confine, gitops, runners

# every route the reviews listed: shared config, hooks, info/attributes, a new main `commondir`, other worktrees'
# metadata and `.git` pointers, this worktree's pointer, renaming the repository or the integration folder and making
# a new one there, files in the owner's home that run code later, a socket folder, and a detached survivor that must
# be gone when the run ends (it would write the config a minute later)
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
late = "import sys, time\nopen('survivor.txt', 'w').write('started')\ntime.sleep(60)\nopen(sys.argv[1] + '/config', 'a')"
subprocess.Popen([sys.executable, "-c", late, common, "jarves-1592-survivor"], start_new_session=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not os.path.exists("survivor.txt"):
    pass
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


def _survivor_alive() -> bool:
    """Is the detached survivor still running anywhere on the host? Read from /proc, no waiting."""
    for d in Path("/proc").iterdir():
        try:
            if d.name.isdigit() and b"jarves-1592-survivor" in (d / "cmdline").read_bytes().split(b"\0"):
                return True                       # an exact argument, not text inside another command line
        except OSError:
            continue
    return False


def _attack(repo: Path, integ: Path, wt: Path, sock: Path) -> list[str]:
    return [sys.executable, "-c", ATTACK, str(repo / ".git"), str(integ), str(wt / ".git"), str(sock)]


@pytest.mark.linux_only
@pytest.mark.userns
def test_a_check_cannot_change_any_shared_git_file_even_from_a_survivor(project):
    repo, integ, wt, sock = project
    before = _state(repo, integ, wt, sock)
    ok, out = gitops.run_check(wt, _attack(repo, integ, wt, sock))
    assert ok and "denied" in out and "WROTE" not in out and "socket hidden" in out, out
    assert (wt / "survivor.txt").read_text() == "started" and not _survivor_alive()   # it died with the run
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
    """A stand-in agent: it runs the check it is allowed (sys.argv[1], the absolute script path), like Claude's Bash."""
    name = "fake"

    def __init__(self, code: str, env: dict[str, str] | None = None) -> None:
        super().__init__()
        self.code, self.seen, self.env = code, {}, env

    def build(self, prompt, cwd, **kw) -> runners.Spec:
        self.seen.update(prompt=prompt, check_argv=kw.get("check_argv"))
        env = self.env if self.env is not None else gitops.child_env()
        return runners.Spec([sys.executable, "-c", self.code, *(kw.get("check_argv") or [])], None, env)

    def parse(self, stdout: str, rc: int) -> runners.Result:
        return runners.Result(ok=rc == 0, text=stdout)


@pytest.mark.linux_only
@pytest.mark.userns
def test_an_agent_runs_the_check_only_through_the_confined_jarves_check_script(project):
    repo, integ, wt, sock = project
    before = _state(repo, integ, wt, sock)
    agent = _Script("import subprocess, sys; sys.stdout.write(subprocess.run([sys.argv[1]], capture_output=True, "
                    "text=True, env={}).stdout); open('agent.txt', 'w').write('mine')")   # review R4: no PATH needed
    r = agent.run("p", str(wt), check_argv=_attack(repo, integ, wt, sock), timeout=60)
    assert r.ok and "denied" in r.text and "WROTE" not in r.text, (r.error, r.text)
    (shim,) = agent.seen["check_argv"]
    assert os.path.isabs(shim) and shim.endswith("/jarves-check") and f"`{shim}`" in agent.seen["prompt"]
    assert not _survivor_alive() and _state(repo, integ, wt, sock) == before
    assert (wt / "agent.txt").read_text() == "mine"             # the agent itself is not confined
    assert not list(Path(runners.tempfile.gettempdir()).glob("jarves-check-*"))  # the script folder is removed


@pytest.mark.linux_only
def test_a_read_only_agent_gets_no_check_script(project):
    repo, integ, wt, sock = project
    agent = _Script("print(1)")
    agent.run("p", str(wt), check_argv=["python"], read_only=True, timeout=30)
    assert agent.seen["check_argv"] == ["python"] and "jarves-check" not in agent.seen["prompt"]


@pytest.mark.linux_only
def test_the_owners_release_command_is_not_confined(monkeypatch, project):
    """Review R4: the release's confinement scope (signing, credential sockets, publisher caches) is the owner's
    decision; until then it runs as before."""
    repo, integ, wt, sock = project
    monkeypatch.setattr(gitops, "confined", lambda *a, **k: pytest.fail("the release must not be wrapped"))
    out = integ.parent / "released"
    assert gitops.run_check(integ, [sys.executable, "-c", f"open({str(out)!r}, 'w')"], confined_run=False)[0]
    assert out.exists()


@pytest.mark.linux_only
@pytest.mark.userns
def test_a_confined_run_cannot_trace_or_signal_any_process_outside(project):
    repo, integ, wt, sock = project
    code = ("import ctypes, os, sys; libc = ctypes.CDLL(None, use_errno=True); pid = int(sys.argv[1]); "
            "r = libc.ptrace(16, pid, None, None); print('ptrace', r, ctypes.get_errno()); "
            "exec('try:\\n os.kill(pid, 0); print(\\'kill ok\\')\\nexcept OSError as e: print(\\'kill\\', e.errno)'); "
            "print('ppid', os.getppid(), 'me', os.getpid())")
    ok, out = gitops.run_check(wt, [sys.executable, "-c", code, str(os.getpid())])
    assert ok and out == "ptrace -1 3 kill 3 ppid 0 me 1", out                  # ESRCH: our pid does not exist there


@pytest.mark.linux_only
@pytest.mark.userns
def test_a_confined_run_cannot_reach_a_unix_socket_outside_the_hidden_folders(project):
    """Review R2: a daemon socket (Docker, D-Bus) is not protected by a read-only mount; it is covered instead."""
    repo, integ, wt, sock = project
    srv = socket.socket(socket.AF_UNIX)
    path = str(repo.parent / "daemon.sock")
    srv.bind(path)
    srv.listen()
    try:
        code = ("import socket, sys; s = socket.socket(socket.AF_UNIX)\ntry:\n s.connect(sys.argv[1]); print('REACHED')"
                "\nexcept OSError as e:\n print('refused', e.errno)")
        ok, out = gitops.run_check(wt, [sys.executable, "-c", code, path])
        assert ok and out.startswith("refused"), out
    finally:
        srv.close()


@pytest.mark.linux_only
@pytest.mark.userns
def test_a_worktree_with_a_broken_git_pointer_is_still_confined(project):
    """Review R3: when git cannot read the worktree's dirs the check runs confined anyway, never unconfined."""
    repo, integ, wt, sock = project
    (wt / ".git").write_text("gitdir: /nonexistent\n")
    ok, out = gitops.run_check(wt, [sys.executable, "-c", f"open({str(repo / '.git' / 'config')!r}, 'a')"])
    assert not ok and "Read-only file system" in out


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
    bad = {"rw": [str(tmp_path / "missing")], "ro": [], "mask": [], "sockets": []}
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
def test_the_spec_keeps_only_the_worktree_writable_and_hides_run(project, monkeypatch):
    repo, integ, wt, sock = project
    common = (repo / ".git").resolve()
    s = confine.spec(wt, common)
    assert s["rw"] == [str(wt.resolve())] and str(sock) in s["mask"]
    assert s["ro"] == [str(common), str(wt.resolve() / ".git")]
    assert confine.spec(wt)["ro"] == [str(wt.resolve() / ".git")]          # no readable shared git dir: none kept
    monkeypatch.undo()
    assert "/run" in confine.HIDE and "/tmp" in confine.HIDE


@pytest.mark.linux_only
@pytest.mark.userns
def test_the_check_script_runs_with_the_allow_listed_env_not_the_agents(project, monkeypatch):
    """Review R5: a secret in the agent's environment is not passed on to the check's code."""
    repo, integ, wt, sock = project
    monkeypatch.setenv("JARVES_1592_SECRET", "s3cret")
    agent = _Script("import subprocess, sys; sys.stdout.write(subprocess.run([sys.argv[1]], capture_output=True, "
                    "text=True).stdout)", env=dict(os.environ))
    code = "import os; print(repr(os.environ.get('JARVES_1592_SECRET')), 'PATH' in os.environ)"
    r = agent.run("p", str(wt), check_argv=[sys.executable, "-c", code], timeout=60)
    assert r.ok and r.text.strip() == "None True", (r.error, r.text)


def test_claudes_allow_rule_names_the_check_script_by_its_absolute_path(tmp_path):
    """Review R4: the real Claude runner allows exactly the absolute script path (a reset PATH cannot hide it)."""
    shim = str(tmp_path / "jarves-check-x" / runners.SHIM)
    spec = runners.ClaudeRunner().build("p", str(tmp_path), check_argv=[shim])
    assert f"Bash({shim}:*)" in spec.argv


def _probe(monkeypatch, result):
    monkeypatch.setattr(gitops, "_CONFINE", {})
    monkeypatch.setattr(gitops.sys, "platform", "linux")

    def run(*a, **k):
        if isinstance(result, BaseException):
            raise result
        return subprocess.CompletedProcess(a, result, "", "boom")
    monkeypatch.setattr(gitops.subprocess, "run", run)


@pytest.mark.parametrize("result", [subprocess.TimeoutExpired("probe", 30), OSError(12, "ENOMEM"), 1, confine.FAIL])
def test_a_probe_that_fails_for_an_unknown_reason_fails_the_run_closed_and_is_not_cached(monkeypatch, tmp_path, result):
    """Review R1: only a known "this kernel cannot" answer turns confinement off; anything else fails this run."""
    _probe(monkeypatch, result)
    ok, out = gitops.run_check(tmp_path, [sys.executable, "-c", "open('ran.txt', 'w')"])
    assert not ok and out.startswith("check could not be confined") and not (tmp_path / "ran.txt").exists()
    assert "ok" not in gitops._CONFINE                                # the next run probes again


def test_a_kernel_without_user_namespaces_is_reported_once_and_cached(monkeypatch, caplog, tmp_path):
    _probe(monkeypatch, confine.UNSUPPORTED)
    with caplog.at_level(logging.WARNING, logger=gitops.__name__):
        assert gitops.confined(["echo"], tmp_path) == ["echo"] and gitops.confined(["echo"], tmp_path) == ["echo"]
    assert gitops._CONFINE == {"ok": False} and len(caplog.records) == 1


@pytest.mark.parametrize("err", [1, 28, 38])                    # EPERM, ENOSPC (userns sysctl/AppArmor), ENOSYS
def test_confine_reports_a_kernel_that_cannot_with_its_own_exit_code(monkeypatch, err):
    def no(_flags):
        raise OSError(err, os.strerror(err))
    monkeypatch.setattr(confine.os, "unshare", no, raising=False)
    assert confine.main(["{}", "--", "true"]) == confine.UNSUPPORTED


def test_too_many_sockets_fails_the_setup_instead_of_leaving_some_reachable(monkeypatch, tmp_path):
    """Review R2: never a silent cap; more sockets than can be covered means no run."""
    monkeypatch.setattr(confine, "MAX_SOCKETS", 2)
    monkeypatch.setattr(confine.os.path, "exists", lambda p: True)
    rows = "".join(f"0: 00000002 0 10000 0001 01 1 /s/{i}\n" for i in range(3))
    real = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: __import__("io").StringIO("hdr\n" + rows)
                        if p == "/proc/net/unix" else real(p, *a, **k))
    with pytest.raises(OSError, match="more than 2"):
        confine._sockets([])
