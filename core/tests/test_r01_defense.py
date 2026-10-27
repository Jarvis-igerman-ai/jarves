"""R-01 defence (review C residual risk, Issue #17): signed approval answers, hidden typed codes, OS limits around
helper commands and generated-Skill children, and the honest `helper_isolation` status row.

Positive tests only: they check that each protection is ACTIVE (a forged or unsigned answer file does not count, a
tampered pending file does not reveal the code, the OS stops a harmless child that allocates more than its memory
limit or starts more processes than allowed, closing the job ends what is left) and that the owner's legitimate
flows (dashboard, CLI, popup) still work. Harmless child programs only (print, allocate, sleep); no network."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarves import approval_keys as akeys
from jarves import approvals as appr
from jarves import isolation as iso
from jarves.config import Config
from jarves.skills import helper as hp

from conftest import until

WIN = sys.platform == "win32"
# the real interpreter (a Windows venv python.exe is a launcher that starts a second process)
PY = getattr(sys, "_base_executable", "") or sys.executable
if not os.path.isfile(PY):
    PY = sys.executable


def dashboard_open(home):
    st, body = appr.api_approvals(SimpleNamespace(home=home, cfg=Config()), None, None)
    assert st == 200
    return body


async def next_request(svc) -> appr.Approval:
    ev, box = asyncio.Event(), []
    svc.on_ask.append(lambda a: (box.append(a), ev.set()))
    await asyncio.wait_for(ev.wait(), 10)
    svc.on_ask.clear()
    return box[0]


@pytest.fixture(autouse=True)
def _fresh_isolation_status():
    iso.reset_status()
    iso._posix_probes.clear()
    yield
    iso.reset_status()
    iso._posix_probes.clear()


def answers_dir(home) -> Path:
    return home / "approvals" / "answers"


async def drop_answer(home, svc, aid, body: dict) -> None:
    """Write an answer file the way another process would, then wait until the Core's poller has consumed it."""
    p = answers_dir(home) / f"{aid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body), encoding="utf-8")
    await until(lambda: not p.exists(), timeout=10)


# ====================================================================== PIECE 1: signed answers, hidden codes
async def test_pending_file_holds_no_plain_typed_code_but_the_owner_still_sees_it(home):
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    task = asyncio.create_task(svc.ask("format", typed=True, risk="high", kind="shell"))
    a = await next_request(svc)
    raw = json.loads((home / "approvals" / "pending.json").read_text(encoding="utf-8"))
    row = raw["pending"][0]
    assert "typed_code" not in row and row["typed"] is True
    assert a.typed_code not in [str(v) for v in row.values()]
    assert row["typed_salt"] and row["typed_hash"] and row["typed_sealed"]
    # the owner's dashboard / CLI in another process (they can read the key file) still show it
    assert appr.read_pending_file(home)[0]["typed_code"] == a.typed_code
    assert appr.read_pending_file(home, reveal=False)[0].get("typed_code") is None
    assert appr.submit_answer(home, a.id, True, channel="cli", typed=a.typed_code) == (True, "")
    assert await asyncio.wait_for(task, 10) is True


async def test_a_tampered_pending_file_reveals_no_code_and_approves_nothing(home):
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    task = asyncio.create_task(svc.ask("format", typed=True, risk="high", kind="shell"))
    a = await next_request(svc)
    pf = home / "approvals" / "pending.json"
    d = json.loads(pf.read_text(encoding="utf-8"))
    import hashlib
    d["pending"][0]["typed_salt"] = "00" * 8                                # another salt: unseals to garbage
    d["pending"][0]["typed_hash"] = hashlib.sha256(b"1111").hexdigest()     # a hash made without the key
    pf.write_text(json.dumps(d), encoding="utf-8")
    assert appr.read_pending_file(home)[0]["typed_code"] == ""             # nothing to show, nothing leaked
    assert appr.submit_answer(home, a.id, True, channel="cli", typed="1111")[0] is False
    # an older-style file with the code in plain text is not trusted either
    d["pending"][0]["typed_code"] = "1111"
    pf.write_text(json.dumps(d), encoding="utf-8")
    assert appr.read_pending_file(home)[0]["typed_code"] == ""
    assert svc.has_pending()
    svc.answer(a.id, True, channel="dashboard", typed=a.typed_code)       # the real code on the real screen: works
    assert await asyncio.wait_for(task, 10) is True


async def test_unsigned_forged_and_replayed_answer_files_are_ignored(home):
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    task = asyncio.create_task(svc.ask("Turn the camera on?", kind="camera", risk="high"))
    a = await next_request(svc)
    base = {"id": a.id, "approved": True, "typed": "", "channel": "dashboard", "ts": round(time.time(), 3)}
    await drop_answer(home, svc, a.id, {"id": a.id, "approved": True, "channel": "dashboard"})   # old format
    await drop_answer(home, svc, a.id, akeys.sign(os.urandom(32), {**base, "nonce": "n1"}))       # other key
    key = akeys.load(home)
    await drop_answer(home, svc, a.id, {**akeys.sign(key, {**base, "nonce": "n2"}), "approved": True,
                                        "channel": "cli"})                                         # edited after
    await drop_answer(home, svc, a.id, akeys.sign(key, {**base, "ts": time.time() - 3600, "nonce": "n3"}))  # stale
    other = "0123456789abcdef"
    await drop_answer(home, svc, other, akeys.sign(key, {**base, "nonce": "n4"}))                 # wrong file name
    assert svc.has_pending() and not task.done()
    assert {"unsigned", "bad signature", "stale", "id does not match the file"} <= svc._quiet   # logged once each
    # replay: the same signed body used a second time does not count again
    d2 = akeys.sign(key, {**base, "approved": False, "nonce": "n5"})
    assert svc._file_problem(d2, answers_dir(home) / f"{a.id}.json") == ""
    assert svc._file_problem(d2, answers_dir(home) / f"{a.id}.json") == "replayed"
    # the legitimate dashboard / CLI path still works
    assert appr.submit_answer(home, a.id, True, channel="dashboard") == (True, "")
    assert await asyncio.wait_for(task, 10) is True
    assert appr.read_history(home, 1)[0]["channel"] == "dashboard"


async def test_core_start_rotates_the_key_and_drops_stale_answer_files(home):
    svc = appr.ApprovalService(home)
    k1 = akeys.load(home)
    answers_dir(home).mkdir(parents=True, exist_ok=True)
    (answers_dir(home) / "0123456789abcdef.json").write_text('{"id": "0123456789abcdef", "approved": true}')
    assert svc.rotate_key() is True
    k2 = akeys.load(home)
    assert k1 and k2 and k1 != k2 and svc._key == k2
    assert not list(answers_dir(home).glob("*.json"))
    assert json.loads((home / "approvals" / "pending.json").read_text(encoding="utf-8"))["pending"] == []
    # an answer signed with the previous key does not count after a restart
    dashboard_open(home)
    task = asyncio.create_task(svc.ask("Send it?", kind="message"))
    a = await next_request(svc)
    await drop_answer(home, svc, a.id, akeys.sign(k1, {"id": a.id, "approved": True, "typed": "",
                                                       "channel": "cli", "ts": time.time(), "nonce": "x"}))
    assert svc.has_pending()
    svc.answer(a.id, False, channel="dashboard")
    assert await task is False


@pytest.mark.windows_only
def test_key_file_has_a_private_dacl_on_windows(home):
    appr.ApprovalService(home).rotate_key()
    sddl = akeys.win_dacl_sddl(akeys.key_path(home))
    sid = akeys.current_user_sid()
    flags, _, rest = sddl[2:].partition("(")
    assert "P" in flags, sddl                                  # protected: nothing inherited from the folder
    aces = [x.lstrip("(") for x in ("(" + rest).split(")") if x]
    # a CI runner is the built-in Administrator (RID 500): Windows prints that account as the alias LA
    allowed = [f"A;;FA;;;{sid}"] + ([ "A;;FA;;;LA" ] if sid.endswith("-500") else [])
    assert len(aces) == 1 and aces[0] in [a for a in allowed], sddl                    # one entry: this user, full control


@pytest.mark.linux_only
def test_key_file_is_mode_0600_on_linux(home):
    appr.ApprovalService(home).rotate_key()
    assert (akeys.key_path(home).stat().st_mode & 0o777) == 0o600
    assert ((home / "approvals").stat().st_mode & 0o777) == 0o700


def test_the_key_never_reaches_the_environment_or_the_pending_file(home):
    svc = appr.ApprovalService(home)
    svc.rotate_key()
    hexkey = akeys.load(home).hex()
    assert all(hexkey not in v for v in os.environ.values())
    assert all(hexkey not in v for v in hp.child_env().values())
    assert hexkey not in (home / "approvals" / "pending.json").read_text(encoding="utf-8")


async def test_cli_list_and_approve_still_work_with_signed_answers(home, capsys):
    from jarves.cli import main
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    task = asyncio.create_task(svc.ask("Install Git?", typed=True, kind="install", risk="high"))
    a = await next_request(svc)
    assert await asyncio.to_thread(main, ["approvals", "list"]) == 0
    out = capsys.readouterr().out
    assert a.id in out and f"--code {a.typed_code}" in out
    assert await asyncio.to_thread(main, ["approvals", "approve", a.id, "--code", "0"]) == 1
    assert await asyncio.to_thread(main, ["approvals", "approve", a.id, "--code", a.typed_code]) == 0
    assert await asyncio.wait_for(task, 10) is True
    assert appr.read_history(home, 1)[0]["channel"] == "cli"


async def test_popup_result_counts_only_with_the_windows_nonce(home):
    """The popup gets the code and a one-time nonce through its stdin; a result file without the nonce is ignored."""
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    ch = appr.PopupChannel(svc)
    task = asyncio.create_task(svc.ask("Popup?", kind="shell", details={"command": "dir"}))
    a = await next_request(svc)
    d = home / "approvals" / "popup"
    d.mkdir(parents=True, exist_ok=True)

    def run_watch(result: dict, nonce: str):
        inp, out = d / f"{a.id}.in.json", d / f"{a.id}.out.json"
        inp.write_text("{}", encoding="utf-8")
        out.write_text(json.dumps(result), encoding="utf-8")
        p = subprocess.Popen([PY, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        ch._watch(a.id, p, inp, out, nonce, b"{}\n")
    await asyncio.to_thread(run_watch, {"id": a.id, "decision": "approve", "typed": ""}, "n" * 32)
    await asyncio.sleep(0)
    assert svc.has_pending() and not task.done()
    await asyncio.to_thread(run_watch, {"id": a.id, "decision": "approve", "typed": "", "nonce": "n" * 32}, "n" * 32)
    assert await asyncio.wait_for(task, 10) is True


def test_popup_window_receives_the_code_on_stdin_not_in_its_file(home, monkeypatch):
    seen = {}

    class P:
        def __init__(self, argv, **kw):
            seen["argv"], seen["stdin"] = argv, kw.get("stdin")
            self.returncode = 0

        def communicate(self, input=None, timeout=None):
            seen["input"] = input
            return b"", b""

        def poll(self):
            return 0

    monkeypatch.setattr(appr.subprocess, "Popen", P)
    svc = appr.ApprovalService(home)
    ch = appr.PopupChannel(svc)
    a = appr.Approval("0123456789abcdef", "t", "p", "", "high", "shell", "model", {}, "4821", False,
                      time.time(), time.time() + 30)
    files = []
    real_write = appr._write_json
    monkeypatch.setattr(appr, "_write_json", lambda path, dd: (files.append(dict(dd)), real_write(path, dd)))
    ch.open(a)
    deadline = time.time() + 10
    while "input" not in seen and time.time() < deadline:
        time.sleep(0.01)
    assert "--stdin" in seen["argv"] and seen["stdin"] == subprocess.PIPE
    assert files and "typed_code" not in files[0] and files[0]["typed"] is True
    assert json.loads(seen["input"])["typed_code"] == "4821" and len(json.loads(seen["input"])["nonce"]) == 32


# ====================================================================== PIECE 2: Windows Job Object
def _win_wait_gone(pid: int, timeout_ms: int = 15000) -> bool:
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.OpenProcess(0x00100000, False, pid)                  # SYNCHRONIZE
    if not h:
        return True                                            # already gone
    try:
        return k.WaitForSingleObject(h, timeout_ms) == 0
    finally:
        k.CloseHandle(h)


def run_limited(code: str, limits: iso.Limits, timeout: float = 60):
    p, g = iso.popen([PY, "-I", "-c", code], limits=limits, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     record=False)
    try:
        out, _ = p.communicate(timeout=timeout)
    finally:
        g.close()
    return p.returncode, out.decode("utf-8", "replace"), g


@pytest.mark.windows_only
def test_job_holds_the_requested_limits_as_the_os_reports_them():
    lim = iso.Limits(memory_mb=256, max_processes=3, cpu_seconds=30)
    p, g = iso.popen([PY, "-I", "-c", "import sys; sys.stdin.readline()"], limits=lim, stdin=subprocess.PIPE,
                     record=False)
    try:
        assert g.state == "active", g.reason
        assert g.job.contains(p.pid)
        q = g.job.query()
        f = q["limit_flags"]
        assert f & iso.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and f & iso.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        assert f & iso.JOB_OBJECT_LIMIT_JOB_MEMORY and f & iso.JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        assert f & iso.JOB_OBJECT_LIMIT_PRIORITY_CLASS and f & iso.JOB_OBJECT_LIMIT_JOB_TIME
        assert not f & (iso.JOB_OBJECT_LIMIT_BREAKAWAY_OK | iso.JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK)   # no breakaway
        assert q["active_process_limit"] == 3 and q["job_memory_limit"] == 256 << 20
        assert q["priority_class"] == iso.BELOW_NORMAL_PRIORITY_CLASS
        assert q["job_user_time_limit"] == 30 * 10_000_000
        ui = q["ui_restrictions"]
        for flag in (iso.UILIMIT_DESKTOP, iso.UILIMIT_READCLIPBOARD, iso.UILIMIT_WRITECLIPBOARD,
                     iso.UILIMIT_GLOBALATOMS, iso.UILIMIT_EXITWINDOWS, iso.UILIMIT_DISPLAYSETTINGS,
                     iso.UILIMIT_SYSTEMPARAMETERS):
            assert ui & flag, hex(ui)
        assert q["active_processes"] == 1 and q["total_processes"] == 1      # it never ran outside the job
        p.stdin.write(b"\n")
        p.stdin.flush()
        assert p.wait(timeout=30) == 0
    finally:
        g.close()


@pytest.mark.windows_only
def test_job_memory_limit_stops_an_allocation_over_it():
    lim = iso.Limits(memory_mb=128, max_processes=2)
    rc, out, g = run_limited("b = bytearray(16 << 20); print('small ok')", lim)
    assert rc == 0 and "small ok" in out and g.state == "active"
    rc, out, g = run_limited("b = bytearray(400 << 20); print('allocated')", lim)
    assert rc != 0 and "MemoryError" in out and "allocated" not in out


@pytest.mark.windows_only
def test_job_process_limit_refuses_one_process_too_many():
    code = ("import subprocess, sys\n"
            "try:\n    subprocess.run([sys.executable, '-c', 'pass'], check=True); print('spawned')\n"
            "except OSError as e:\n    print('refused', getattr(e, 'winerror', e))\n")
    rc, out, _ = run_limited(code, iso.Limits(max_processes=1))
    assert "refused" in out and "spawned" not in out, out
    rc, out, _ = run_limited(code, iso.Limits(max_processes=4))
    assert "spawned" in out, out                                # within the limit a command may start helpers


@pytest.mark.windows_only
def test_kill_on_close_ends_processes_the_command_left_behind():
    code = ("import subprocess, sys\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
            "print(c.pid, flush=True)\n")
    p, g = iso.popen([PY, "-I", "-c", code], limits=iso.Limits(max_processes=4), stdout=subprocess.PIPE,
                     record=False)
    try:
        child = int(p.stdout.readline())
        assert p.wait(timeout=30) == 0
        assert g.job.contains(child)
        for _ in range(100):                      # a launcher stub of the interpreter may still be exiting under load
            if g.job.query()["active_processes"] == 1:
                break
            time.sleep(0.1)
        assert g.job.query()["active_processes"] == 1                              # the sleeper is still there
    finally:
        g.close()
    assert _win_wait_gone(child), "kill-on-close did not end the leftover process"


@pytest.mark.windows_only
def test_helper_timeout_ends_the_whole_tree(home):
    code = ("import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
            "print('child', c.pid, flush=True)\n"
            "time.sleep(600)\n")
    prop = hp.Proposal("sleeper", home, [PY, "-I", "-c", code], "normal", [])
    r = hp.run_blocking(prop, 3, home / "helper" / "runs" / "t.log", iso.Limits(max_processes=4))
    assert r.timed_out and r.isolation == "active"
    child = int(r.tail.split("child", 1)[1].split()[0])
    assert _win_wait_gone(child)


@pytest.mark.windows_only
async def test_generated_skill_child_starts_inside_a_one_process_job():
    proc, g = await iso.exec_async(PY, "-I", "-S", "-c", "import sys; sys.stdin.readline()",
                                   limits=iso.SKILL_LIMITS, stdin=asyncio.subprocess.PIPE)
    try:
        assert g.state == "active", g.reason
        q = g.job.query()
        assert q["active_process_limit"] == 1 and q["process_memory_limit"] == 512 << 20
        assert q["ui_restrictions"] & iso.UILIMIT_HANDLES and q["total_processes"] == 1
        proc.stdin.write(b"\n")
        await proc.stdin.drain()
        assert await asyncio.wait_for(proc.wait(), 30) == 0
    finally:
        g.close()


async def test_a_real_generated_skill_call_still_works_inside_its_limits():
    from jarves.skills import generated as gen

    async def broker(op, args):
        return None
    r = await gen.run_sandboxed("def go(args, ctx):\n    return {'message': str(sum(range(10)))}\n", "go", {}, broker)
    assert r.get("ok") is True and r.get("message") == "45", r


# ====================================================================== PIECE 2: POSIX (same interface)
def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                       # a zombie (exited, not reaped yet) counts as gone
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


@pytest.mark.linux_only
def test_posix_launcher_sets_rlimits_and_a_new_session():
    code = ("import os, resource\n"
            "print(resource.getrlimit(resource.RLIMIT_DATA)[0], os.getsid(0) == os.getpid(), "
            "resource.getrlimit(resource.RLIMIT_NPROC)[0] != resource.RLIM_INFINITY)\n")
    rc, out, g = run_limited(code, iso.Limits(memory_mb=256))
    assert rc == 0 and out.split() == [str(256 << 20), "True", "True"], out
    assert g.state in ("active", "degraded") and g.group


@pytest.mark.linux_only
def test_posix_data_limit_stops_an_allocation_over_it():
    rc, out, _ = run_limited("b = bytearray(16 << 20); print('small ok')", iso.Limits(memory_mb=128))
    assert rc == 0 and "small ok" in out
    rc, out, _ = run_limited("b = bytearray(400 << 20); print('allocated')", iso.Limits(memory_mb=128))
    assert rc != 0 and "MemoryError" in out


def _read_pid(stream, timeout: float = 30.0) -> int:
    """One line from a child's stdout pipe. The pipe can be non-blocking (EAGAIN -> BlockingIOError / None) when the child
    has not printed yet (#661): retry until the line arrives instead of failing on the race."""
    end = time.monotonic() + timeout
    while True:
        try:
            line = stream.readline()
        except BlockingIOError:
            line = None
        if line:
            return int(line)
        if time.monotonic() > end:
            raise TimeoutError("the child printed no pid")
        time.sleep(0.05)


@pytest.mark.linux_only
async def test_posix_close_ends_the_process_group():
    code = ("import subprocess, sys\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
            "print(c.pid, flush=True)\n")
    p, g = iso.popen([PY, "-I", "-c", code], limits=iso.Limits(), stdout=subprocess.PIPE, record=False)
    child = _read_pid(p.stdout)
    assert p.wait(timeout=30) == 0 and _alive(child)
    g.close()
    await until(lambda: not _alive(child), timeout=15)


# ====================================================================== PIECE 2: honest degradation
def test_when_no_job_can_be_made_the_command_still_runs_and_says_so(monkeypatch):
    if WIN:
        def broken(limits):
            raise OSError(5, "access denied (test)")
        monkeypatch.setattr(iso, "WinJob", broken)
    else:
        monkeypatch.setattr(iso, "_probe", lambda limits: {"state": "unavailable", "reason": "no rlimits (test)"})
    seen = []
    monkeypatch.setitem(iso.listeners, "test", lambda st: seen.append(st))
    p, g = iso.popen([PY, "-I", "-c", "print('ran')"], limits=iso.Limits(), stdout=subprocess.PIPE)
    out, _ = p.communicate(timeout=30)
    g.close()
    assert p.returncode == 0 and b"ran" in out
    assert g.state == "unavailable" and g.reason
    st = iso.status()
    assert st["state"] == "unavailable" and st["source"] == "last run"
    assert seen and seen[-1]["state"] == "unavailable"
    assert iso.health_status(st)[0] == "unavailable"


# ====================================================================== PIECE 5: status row + stronger confirmation
class FakeRegistry:
    def __init__(self):
        self.asks = []

    async def ask(self, prompt, **kw):
        self.asks.append(kw)
        return False


async def test_helper_asks_for_the_typed_code_when_isolation_is_unavailable(home, monkeypatch):
    reg = FakeRegistry()
    skill = hp.HelperSkill(reg, home)
    prop = hp.Proposal("git status", home, ["git", "status"], "normal", [])
    monkeypatch.setattr(iso, "_probe", lambda limits: {"state": "active", "reason": "test"})
    await skill._approve(prop, "check the repo", "shell")
    assert reg.asks[-1]["typed"] is False and reg.asks[-1]["voice"] is True
    assert reg.asks[-1]["details"]["isolation"].startswith("active")
    iso.reset_status()
    monkeypatch.setattr(iso, "_probe", lambda limits: {"state": "unavailable", "reason": "no job (test)"})
    await skill._approve(prop, "check the repo", "shell")
    assert reg.asks[-1]["typed"] is True and reg.asks[-1]["voice"] is False and reg.asks[-1]["risk"] == "high"
    assert reg.asks[-1]["details"]["isolation"].startswith("unavailable")


async def test_status_row_in_the_running_core_matches_reality(core):
    c = core.health.components.get("helper_isolation")
    assert c is not None
    st = iso.status(iso.Limits.from_config(core.cfg.helper))
    assert c.status == iso.health_status(st)[0] and c.detail.startswith(st["state"])
    if WIN:
        assert st["state"] == "active" and c.status == "ok", c.detail
    # a run that degrades flips the live row (and back when it is active again)
    iso._record("unavailable", "test reason", iso.HELPER_LIMITS)
    assert core.health.components["helper_isolation"].status == "unavailable"
    iso._record("active", "job object: test", iso.HELPER_LIMITS)
    assert core.health.components["helper_isolation"].status == "ok"


def test_status_row_in_features_dashboard_and_selfcheck(home, capsys, monkeypatch):
    from jarves import dashboard, features, selfcheck
    from jarves.cli import main
    assert main(["features", "list"]) == 0
    out = capsys.readouterr().out
    st = iso.status()
    assert f"helper_isolation       {st['state'].upper()}" in out
    ctx = SimpleNamespace(home=home, cfg=Config())
    code, body = features.api_features(ctx, None, None)
    assert code == 200 and body["helper_isolation"]["state"] == st["state"]
    store = SimpleNamespace(tool_stats=lambda days: {}, skills=lambda: {})
    code, body = dashboard.api_skills(SimpleNamespace(home=home, cfg=Config(), store=store), None, None)
    assert body["components"]["helper_isolation"]["status"] == iso.health_status(st)[0]
    # the self-check reports it when it is not active
    iso.reset_status()
    monkeypatch.setattr(iso, "_probe", lambda limits: {"state": "unavailable", "reason": "no job (test)"})
    chk = selfcheck.SelfCheck(home, Config(), components=lambda: {})
    found = []
    chk._health(found)
    f = [x for x in found if x.fingerprint == "health:helper_isolation"]
    assert f and "unavailable" in f[0].title and "typed code" in f[0].fix
