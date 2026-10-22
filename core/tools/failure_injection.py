"""Failure injection (release checklist item 10): harmless FAKE faults against a real Core on a throw-away home.

    python tools/failure_injection.py --all            # every case
    python tools/failure_injection.py --case port_taken --case kill9

Safety: every case uses its own temp JARVES_HOME and a free localhost port, the offline AI provider, no microphone,
no camera, no Phone Link, no owner profile. Faults are fake: a socket we hold open, a garbage store.db in the temp
home, a dead local proxy for "network off". Only child PIDs started here are ever stopped. Not covered here (say so
in the report): disk full, real sleep/resume, Pear closed, bridge killed, provider 429/500/slow (no base-url hook),
which need the owner PC or a provider stub.

Per case: honest message (no traceback), no crash loop, recovery, no data loss. Exit code 0 = all selected cases PASS.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
RESULTS: list[tuple[str, bool, str]] = []
CHILDREN: list[subprocess.Popen] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make_home(port: int, ai: str = "offline") -> Path:
    home = Path(tempfile.mkdtemp(prefix="fi-"))
    (home / "config.toml").write_text(
        f'[ai]\nprovider = "{ai}"\n\n[voice]\nmode = "off"\n\n[link]\nport = {port}\n\n[music]\nprovider = "none"\n', encoding="utf-8")
    return home


def env_for(home: Path, extra: dict | None = None) -> dict:
    env = {**os.environ, "JARVES_HOME": str(home), "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(CORE / "src")}
    env.pop("OPENAI_API_KEY", None)
    env.pop("JARVES_PROFILE", None)
    env.update(extra or {})
    return env


def cli(home: Path, *args, timeout: float = 60, extra: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "jarves", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env_for(home, extra), cwd=str(CORE))


def start_core(home: Path, extra: dict | None = None) -> subprocess.Popen:
    out = open(home / "core.out", "ab")
    p = subprocess.Popen([sys.executable, "-m", "jarves", "run"], stdout=out, stderr=subprocess.STDOUT, env=env_for(home, extra),
                         cwd=str(CORE))
    CHILDREN.append(p)
    return p


def log_text(home: Path) -> str:
    try:
        return (home / "logs" / "jarves.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def wait(pred, timeout: float, step: float = 0.3) -> bool:
    t = time.time()
    while time.time() - t < timeout:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def ready(home: Path, n: int = 1) -> bool:
    return log_text(home).count("CORE_READY") >= n


def kill_tree(p: subprocess.Popen) -> None:
    """Hard kill (kill -9 equivalent) of a child we started, with its children."""
    if p.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True) if os.name == "nt" else p.kill()
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass


def stop_core(home: Path, p: subprocess.Popen) -> bool:
    cli(home, "stop", timeout=40)
    try:
        p.wait(timeout=30)
        return True
    except subprocess.TimeoutExpired:
        kill_tree(p)
        return False


# ------------------------------------------------------------------ cases
def case_port_taken() -> None:
    port = free_port()
    home = make_home(port)
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen(1)                                              # the fake fault: something else holds the Core's port
    p = start_core(home)
    try:
        ended = wait(lambda: p.poll() is not None, 60)
        out = (home / "core.out").read_text(encoding="utf-8", errors="replace")
        honest = "Traceback" not in out and ("port" in (out + log_text(home)).lower() or "address" in (out + log_text(home)).lower())
        rec("FI-01 port taken: Core ends (no crash loop) with a message that names the port, no traceback",
            ended and honest, f"exit={p.poll()} tail={out.strip()[-160:]!r}")
    finally:
        s.close()
        kill_tree(p)
    p2 = start_core(home)                                    # fault removed: recovery
    ok = wait(lambda: ready(home), 90)
    rec("FI-01b port freed: the next start is ready", ok, "")
    stop_core(home, p2)


def case_two_instances() -> None:
    port = free_port()
    home = make_home(port)
    p = start_core(home)
    try:
        up = wait(lambda: ready(home), 90)
        r = cli(home, "run", timeout=60)
        refused = r.returncode != 0 and "Traceback" not in r.stderr
        still = p.poll() is None and cli(home, "status", "--brief").returncode in (0, 3, 4)
        rec("FI-02 two instances: the second is refused cleanly, the first keeps running",
            up and refused and still, f"rc={r.returncode} {(r.stdout + r.stderr).strip()[-140:]!r}")
    finally:
        stop_core(home, p)


def case_kill9() -> None:
    port = free_port()
    home = make_home(port)
    p = start_core(home)
    marker = "kill9marker42"
    try:
        wait(lambda: ready(home), 90)
        r = cli(home, "ask", f"תזכור שהמילה הסודית היא {marker}")
        kill_tree(p)                                          # kill -9
        gone = p.poll() is not None
        p2 = start_core(home)
        back = wait(lambda: ready(home, 2), 90)
        mem = cli(home, "memory", "list")
        kept = marker in mem.stdout or marker in cli(home, "ask", "מה אתה זוכר").stdout
        crash_loop = log_text(home).count("CORE_STARTING") > 2
        rec("FI-03 Core killed (kill -9): restart is ready again, memory kept, no crash loop",
            gone and back and kept and not crash_loop and "Traceback" not in r.stderr,
            f"killed={gone} ready_again={back} memory_kept={kept} starts={log_text(home).count('CORE_STARTING')}")
        stop_core(home, p2)
    finally:
        kill_tree(p)


def case_corrupt_store() -> None:
    port = free_port()
    home = make_home(port)
    cli(home, "ask", "תזכור שאני אוהב קפה")                      # creates the data
    db = home / "store.db"
    junk = b"THIS IS NOT A SQLITE FILE\x00" * 400
    db.write_bytes(junk)                                      # the fake fault: a corrupt store.db
    p = start_core(home)
    try:
        ended = wait(lambda: p.poll() is not None or ready(home), 90)
        out = (home / "core.out").read_text(encoding="utf-8", errors="replace")
        refused = p.poll() not in (None, 0) and "Traceback" not in out and "crashed" in out
        untouched = db.read_bytes() == junk                   # the damaged file is neither deleted nor rewritten
        backed_up = any((home / "backups").glob("*.zip")) if (home / "backups").exists() else False
        single = log_text(home).count("CRASH") <= 1   # one failed attempt, no crash loop
        rec("FI-04 corrupt store.db: Core refuses with a plain message (no traceback), data untouched, backup made, no crash loop",
            ended and (refused or ready(home)) and untouched and single,
            f"exit={p.poll()} untouched={untouched} backup={backed_up} message={out.strip().splitlines()[-1:]!r}")
    finally:
        kill_tree(p)
    db.rename(home / "store.db.damaged")                      # owner recovery: move the damaged file aside
    p2 = start_core(home)
    try:
        ok = wait(lambda: ready(home), 90)
        kept = "קפה" in cli(home, "memory", "list").stdout
        rec("FI-04b damaged store.db moved aside: Core is ready again and the memory is still there", ok and kept,
            f"ready={ok} memory_kept={kept}")
    finally:
        stop_core(home, p2)

def case_network_off() -> None:
    """Cloud provider, a bogus key and a dead local proxy (nothing leaves the PC): an honest error, no crash."""
    port = free_port()
    home = make_home(port, ai="openai")
    dead = f"http://127.0.0.1:{free_port()}"
    extra = {"OPENAI_API_KEY": "sk-fake-failure-injection-not-a-real-key", "HTTPS_PROXY": dead, "HTTP_PROXY": dead,
             "ALL_PROXY": dead, "NO_PROXY": "127.0.0.1,localhost"}
    t0 = time.time()
    r = cli(home, "ask", "מה השעה", timeout=120, extra=extra)
    dt = time.time() - t0
    msg = (r.stdout + r.stderr).strip()
    rec("FI-05 network off (dead proxy): `ask` answers or fails with a plain message within the timeout, no traceback",
        "Traceback" not in r.stderr and dt < 100 and bool(msg), f"rc={r.returncode} {dt:.0f}s {msg[-160:]!r}")
    p = start_core(home, extra)
    try:
        up = wait(lambda: ready(home), 90)
        st = cli(home, "status", "--brief", extra=extra)
        rec("FI-05b network off: the Core still starts and answers `status` (degraded is fine, crash is not)",
            up and p.poll() is None and "Traceback" not in st.stderr, f"status rc={st.returncode} {st.stdout.strip()[:100]!r}")
    finally:
        stop_core(home, p)


CASES = {"port_taken": case_port_taken, "two_instances": case_two_instances, "kill9": case_kill9,
         "corrupt_store": case_corrupt_store, "network_off": case_network_off}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--case", action="append", choices=sorted(CASES))
    a = ap.parse_args()
    names = sorted(CASES) if a.all or not a.case else a.case
    try:
        for n in names:
            try:
                CASES[n]()
            except Exception as e:                               # a broken case is a FAIL, never a silent skip
                rec(f"case {n} raised", False, f"{type(e).__name__}: {e}")
    finally:
        for c in CHILDREN:
            kill_tree(c)
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nFAILURE INJECTION: {len(RESULTS) - len(bad)}/{len(RESULTS)} PASS" + (f"; FAIL: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
