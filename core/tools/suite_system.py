"""System-level pre-use campaign: silent, headless, offline except the ChatGPT-plan text checks.

    uv run --no-sync python tools/suite_system.py [--skip-pytest] [--soak-min 10] [--no-plan]

Writes docs/reviews/PRE_USE_TEST_REPORT_SYSTEM.md. Never prints the API key. Never plays sound.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parent
REAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/state"))) / "jarves"
RESULTS: list[tuple[str, bool, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)


def run(args, home: Path | None = None, timeout=120, env_extra=None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("OPENAI_API_KEY", None)                              # tests that need the key load it themselves
    if home is not None:
        env["JARVES_HOME"] = str(home)
    env.update(env_extra or {})
    return subprocess.run(["uv", "--directory", str(CORE), "run", "--no-sync", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)


def jarves(args, home, timeout=120, **kw):
    return run(["jarves", *args], home, timeout, **kw)


def write_cfg(home: Path, text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")


def rss_mb() -> float:
    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("a", ctypes.c_size_t), ("b", ctypes.c_size_t),
                    ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]
    if sys.platform != "win32":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    from ctypes import wintypes
    k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    c = PMC()
    c.cb = ctypes.sizeof(c)
    psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize / 1e6


def read_key() -> str:
    try:
        for line in (REAL / "secrets.env").read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


# ------------------------------------------------------------------ checks
def s01_pytest():
    r = subprocess.run(["uv", "--directory", str(CORE), "run", "--no-sync", "pytest", "-q", "-p", "no:cacheprovider"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    tail = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l][-1:]
    rec("S01 offline pytest suite", r.returncode == 0, tail[0] if tail else r.stdout[-120:])


def godot_exe() -> str | None:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
    for p in base.glob("GodotEngine.GodotEngine_*/Godot_v4.7.2-stable_win64_console.exe"):
        return str(p)
    return os.environ.get("GODOT_EXE")


def s02_godot():
    g = godot_exe()
    if not g:
        rec("S02 Godot headless checks", False, "no Godot found")
        return
    gd = REPO / "godot"
    out = {}
    for name, cmd in (("viewer", [g, "--headless", "--path", str(gd), "--quit-after", "3000", "--", "--check", "--no-core"]),
                      ("face", [g, "--headless", "--path", str(gd), "--quit-after", "3000", "res://face/face_app.tscn", "--",
                                "--check"])):
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        m = re.search(r"(?:FACE )?CHECK RESULT: (\w+)", r.stdout)
        out[name] = m.group(1) if m else "no result"
    rec("S02 Godot viewer + face self-tests (headless)", all(v == "PASS" for v in out.values()), str(out))
    r = subprocess.run(["uv", "--directory", str(CORE), "run", "--no-sync", "pytest", "-q", "tests/test_face_protocol_godot.py"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
                       env={**os.environ, "GODOT_EXE": g})
    rec("S03 real face survives malformed protocol messages", r.returncode == 0 and "passed" in r.stdout,
        (r.stdout.strip().splitlines() or [""])[-1])


def s04_doctor():
    r = jarves(["doctor"], REAL, 90)
    fails = [l for l in r.stdout.splitlines() if l.startswith("[FAIL]")]
    rec("S04 jarvis doctor (key, ChatGPT, mic, wake model, Pear)", not fails,
        "; ".join(f[7:70] for f in fails) or r.stdout.strip().splitlines()[-1])


def s05_cli_smoke():
    home = Path(tempfile.mkdtemp(prefix="sys-cli-"))
    write_cfg(home, '[ai]\nprovider = "offline"\n\n[voice]\nmode = "off"\n\n[music]\nprovider = "none"\n')
    bad = []
    cases = [(["status"], (0, 1)), (["memory", "list"], (0,)), (["logs", "-n", "5"], (0, 1)), (["chatgpt", "status"], (0, 1)),
             (["key", "--show-path"], (0,)), (["wake", "--help"], (0,)), (["doctor", "--help"], (0,)),
             (["music", "status"], (0, 1)), (["ask", "מה השעה"], (0,)), (["ask", "תזכור שאני אוהב קפה"], (0,)),
             (["ask", "מה אתה זוכר"], (0,)), (["memory", "list"], (0,))]
    outs = {}
    for a, okc in cases:
        r = jarves(a, home, 60)
        outs[" ".join(a)] = r.stdout
        if r.returncode not in okc or "Traceback" in r.stderr:
            bad.append(f"{' '.join(a)} -> rc {r.returncode} {r.stderr.strip()[-80:]}")
    persisted = "קפה" in outs["memory list"] or "קפה" in outs.get("ask מה אתה זוכר", "")
    rec("S05 CLI commands run cleanly (12 commands, offline profile)", not bad, "; ".join(bad)[:200] or "all ok")
    rec("S06 memory written by one process is read by the next (persistence)", persisted)


def s07_config_errors():
    home = Path(tempfile.mkdtemp(prefix="sys-cfg-"))
    cases = {
        "invalid enum": '[ai]\nconnection = "nope"\n',
        "secret in config": 'api_key = "sk-test"\n',
        "LAN host": '[link]\nhost = "0.0.0.0"\n',
        "unknown key": '[voice]\nbogus = 1\n',
        "bad TOML": "[ai\n",
    }
    bad = []
    for name, txt in cases.items():
        write_cfg(home, txt)
        r = jarves(["status"], home, 60)
        if r.returncode != 2 or "CONFIG ERROR" not in r.stderr:
            bad.append(f"{name}: rc={r.returncode}")
    rec("S07 bad configs are refused with a clear error (5 kinds)", not bad, "; ".join(bad) or "all rc=2 CONFIG ERROR")


def s08_start_stop_cycles():
    home = Path(tempfile.mkdtemp(prefix="sys-cycle-"))
    port = 8787
    write_cfg(home, f'[ai]\nprovider = "offline"\n\n[voice]\nmode = "off"\n\n[link]\nport = {port}\n\n[music]\nprovider = "none"\n')
    times, bad = [], []
    for i in range(5):
        env = {**os.environ, "JARVES_HOME": str(home), "PYTHONIOENCODING": "utf-8"}
        env.pop("OPENAI_API_KEY", None)
        t0 = time.time()
        p = subprocess.Popen(["uv", "--directory", str(CORE), "run", "--no-sync", "jarves", "run"], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log = home / "logs" / "jarves.log"
        ok = False
        for _ in range(120):
            time.sleep(0.25)
            if log.exists() and log.read_text(encoding="utf-8", errors="replace").count("CORE_READY") > i:
                ok = True
                break
        t_ready = time.time() - t0
        r = jarves(["stop"], home, 30)
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
            bad.append(f"cycle {i}: did not exit")
        stopped = log.read_text(encoding="utf-8", errors="replace").count("CORE_STOPPED") > i
        import socket
        s = socket.socket()
        free = s.connect_ex(("127.0.0.1", port)) != 0
        s.close()
        if not (ok and stopped and free):
            bad.append(f"cycle {i}: ready={ok} stopped={stopped} port_free={free}")
        times.append(round(t_ready, 1))
    rec("S08 Core start/stop x5: ready, clean stop, port freed", not bad, f"ready in {times} s" + (f"; {bad}" if bad else ""))


def s09_secret_scan():
    key = read_key()
    if not key:
        rec("S09 API key never appears in logs/diag/repo", False, "no key found to scan for")
        return
    roots = [REAL / "logs", Path(tempfile.gettempdir())]
    hits, scanned = 0, 0
    for root in roots:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in (".log", ".json", ".txt", ".toml", ".zip", "") and p.stat().st_size < 30_000_000:
                if "secrets.env" in p.name:
                    continue
                try:
                    data = p.read_bytes()
                except OSError:
                    continue
                scanned += 1
                if key.encode() in data:
                    hits += 1
    g = subprocess.run(["git", "-C", str(REPO), "grep", "-n", "-I", "-E", r"sk-[A-Za-z0-9_-]{20,}"], capture_output=True, text=True)
    repo_hits = [l for l in g.stdout.splitlines() if "test" not in l.lower() and "sk-***" not in l]
    d = jarves(["diag", "--out", str(Path(tempfile.gettempdir()) / "sys-diag.zip")], REAL, 60)
    import zipfile
    z = zipfile.ZipFile(Path(tempfile.gettempdir()) / "sys-diag.zip")
    diag_hit = any(key.encode() in z.read(n) for n in z.namelist())
    rec("S09 API key never appears in logs, temp state, diag bundle or repo", hits == 0 and not repo_hits and not diag_hit,
        f"scanned {scanned} files, key hits {hits}, diag hit {diag_hit}, repo patterns {len(repo_hits)}")


def s10_diag_masks_content():
    r = jarves(["diag", "--out", str(Path(tempfile.gettempdir()) / "sys-diag2.zip")], REAL, 60)
    import zipfile
    z = zipfile.ZipFile(Path(tempfile.gettempdir()) / "sys-diag2.zip")
    recent = z.read("recent.log").decode("utf-8", "replace")
    leaked = re.findall(r'"text": "(?!<)[^"]{4,}"', recent)
    rec("S10 `jarvis diag` support bundle masks conversation text", not leaked, f"{len(recent.splitlines())} lines, {len(leaked)} unmasked text fields")


def s11_log_rotation():
    home = Path(tempfile.mkdtemp(prefix="sys-rot-"))
    code = ("from pathlib import Path; from jarves import logs; "
            f"logs.setup(Path(r'{home}')/'logs','INFO',max_bytes=20000,backups=3,console=False); "
            "[logs.info('t','X','y'*200, i=i) for i in range(2000)]")
    r = run(["python", "-c", code], home, 60)
    files = list((home / "logs").glob("jarves.log*"))
    size = sum(f.stat().st_size for f in files)
    rec("S11 log rotation is bounded (20 KB x 4 files max)", len(files) <= 4 and size <= 4 * 20500 + 2000,
        f"{len(files)} files, {size} bytes")


def s12_chatgpt_plan_text(no_plan: bool):
    if no_plan:
        rec("S12 ChatGPT-plan text path (real)", True, "skipped (--no-plan)")
        return
    st = jarves(["chatgpt", "status"], REAL, 60).stdout
    if '"signed_in": true' not in st:
        rec("S12 ChatGPT-plan text path (real)", True, "NEEDS OWNER PLAN (not a failure): sign in once with `jarvis chatgpt login`, "
            "then re-run `uv run --no-sync python tools/suite_system.py --skip-pytest`")
        return
    qs = [("מה השעה", "command, no AI"), ("ספר לי בדיחה קצרה", "plan text"), ("כמה זה 17 כפול 23", "plan text"),
          ("מי ראש ממשלת בריטניה כרגע", "plan text + web search?"), ("מה מזג האוויר בתל אביב היום", "web search?")]
    rows, bad = [], []
    for q, what in qs:
        t0 = time.time()
        r = jarves(["ask", q], REAL, 180)
        dt = time.time() - t0
        out = r.stdout.strip()
        rows.append(f"{what}: {dt:.1f}s -> {out[:70]!r}")
        text = re.sub(r"^\[[^\]]*\]\s*", "", out).strip()
        if (r.returncode != 0 and "offline" in out) or text in ("", "…", "...") or len(text) < 4:
            bad.append(f"{q}: EMPTY/FAILED answer {out[:90]!r}")          # an empty "…" answer is a FAIL (found 19 Oct)
        print("    ask:", q, "|", f"{dt:.1f}s", "|", out[:140], flush=True)
    rec("S12 ChatGPT-plan text path (real: 5 questions via the user's plan)", not bad, "; ".join(bad)[:300] or "all answered")


def s13_soak(minutes: float):
    """Core idle with the wake word listening on a virtual mic for N minutes: memory, CPU and log growth."""
    home = Path(tempfile.mkdtemp(prefix="sys-soak-"))
    write_cfg(home, '[ai]\nprovider = "offline"\n\n[voice]\nactivation = "wake_word"\nwake_enabled = true\ngreeting = false\n'
                    'mode = "push_to_talk"\n\n[link]\nport = 8788\n\n[music]\nprovider = "none"\n\n[log]\nlevel = "INFO"\n')
    code = r"""
import asyncio, os, sys, time, json
import numpy as np
sys.path.insert(0, r'%s')
os.environ['JARVES_HOME'] = r'%s'
import jarves.app as app_mod
from jarves.app import Core
from jarves.config import load_config
from jarves.voice.audio import SimAudio
from tools.suite_system import rss_mb

class VMic:
    def __init__(self, *a, **k): self._open = True
    async def mic_chunks(self, rate, chunk_bytes):
        rng = np.random.default_rng(3)
        while self._open:
            await asyncio.sleep(chunk_bytes / 2 / rate)
            yield rng.integers(-40, 40, chunk_bytes // 2).astype('<i2').tobytes()
    def close(self): self._open = False
    def enqueue(self, p): pass
    def queued_s(self): return 0.0
    def flush(self): return 0
    def stats(self): return {}
    async def run_player(self, rate): await asyncio.sleep(1e9)

class NoSTT:
    loaded_name = 'none'
    async def transcribe(self, a): return ''
class NoTTS:
    def check(self): return True, 'local-fallback', 'none'
    async def synth(self, t): return np.zeros(2400, np.float32), 24000

async def main():
    app_mod.StreamingAudio = VMic
    cfg, _ = load_config()
    c = Core(cfg, r'%s' and __import__('pathlib').Path(r'%s'), audio=SimAudio(), console_logs=False, use_real_audio=True, stt=NoSTT(), tts=NoTTS())
    await c.start()
    t0 = time.time(); cpu0 = time.process_time(); samples = []
    total = %f * 60
    while time.time() - t0 < total:
        await asyncio.sleep(30)
        samples.append((round(time.time() - t0), round(rss_mb(), 1), round((time.process_time() - cpu0) / (time.time() - t0) * 100, 1),
                        os.path.getsize(os.path.join(r'%s', 'logs', 'jarves.log'))))
    snap = c.snapshot()
    await c.stop()
    print('SOAK ' + json.dumps({'samples': samples, 'wake': snap['components'].get('wake_word', {}).get('status')}))
asyncio.run(main())
""" % (str(CORE), str(home), str(home), str(home), minutes, str(home))
    script = home / "soak.py"
    script.write_text(code, encoding="utf-8")
    env = {**os.environ, "JARVES_HOME": str(home), "PYTHONIOENCODING": "utf-8"}
    env.pop("OPENAI_API_KEY", None)
    r = subprocess.run(["uv", "--directory", str(CORE), "run", "--no-sync", "python", str(script)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=int(minutes * 60 + 240), env=env)
    m = re.search(r"SOAK (\{.*\})", r.stdout)
    if not m:
        rec(f"S13 {minutes:g}-minute idle soak with the wake word listening", False, (r.stderr or r.stdout)[-200:])
        return
    d = json.loads(m.group(1))
    s = d["samples"]
    rss0, rss1 = s[0][1], s[-1][1]
    cpu = max(x[2] for x in s)
    logkb = s[-1][3] / 1024
    ok = rss0 > 20 and rss1 - rss0 < 60 and cpu < 60 and logkb < 800 and d["wake"] == "ok"   # rss0 > 20: a real reading
    rec(f"S13 {minutes:g}-minute idle soak with the wake word listening (virtual mic)", ok,
        f"memory {rss0}->{rss1} MB, peak CPU {cpu}%, log {logkb:.0f} KB, wake {d['wake']}")


# ------------------------------------------------------------------ SW-V2 features on a REAL Core (P0.4)
# One real Core (offline AI: no cost) in a child process with a virtual wake microphone, a stand-in face on the real
# link, the real dashboard over HTTP and the real CLI. Camera checks use the Core's own FakeCamera backend (a
# generated test pattern: a VIRTUAL camera, never the real sensor). Google is NOT connected (no fake Google): the
# calendar checks prove the guardrails of the real not-connected path.
V2_PORT = 8789
V2_CFG = ('[ai]\nprovider = "offline"\n\n[voice]\nactivation = "wake_word"\nwake_enabled = true\ngreeting = false\n'
          'mode = "push_to_talk"\n\n[link]\nport = %d\n\n[music]\nprovider = "none"\n\n[log]\nlevel = "VERBOSE"\n'
          'max_bytes = 50000000\n' % V2_PORT)


class VMic:
    """Virtual microphone (room noise, never a real device) + silent speaker for the child Cores."""
    open_mics = 0

    def __init__(self, *a, **k):
        self._open = True

    async def mic_chunks(self, rate, chunk_bytes):
        import numpy as np
        rng = np.random.default_rng(3)
        VMic.open_mics += 1
        try:
            while self._open:
                await asyncio.sleep(chunk_bytes / 2 / rate)
                yield rng.integers(-40, 40, chunk_bytes // 2).astype("<i2").tobytes()
        finally:
            VMic.open_mics -= 1

    def close(self):
        self._open = False

    def enqueue(self, p):
        pass

    def queued_s(self):
        return 0.0

    def flush(self):
        return 0

    def stats(self):
        return {}

    async def run_player(self, rate):
        await asyncio.sleep(1e9)


class _NoSTT:
    loaded_name = "none"

    async def transcribe(self, a):
        return ""


class _NoTTS:
    def check(self):
        return True, "local-fallback", "none"

    async def synth(self, t):
        import numpy as np
        return np.zeros(2400, np.float32), 24000


def _emit(name: str, ok: bool, detail: str, **data) -> None:
    print("V2 " + json.dumps({"name": name, "ok": bool(ok), "detail": detail, **data}, ensure_ascii=False, default=str),
          flush=True)


async def _until(pred, timeout: float, step: float = 0.1) -> bool:
    t = time.time()
    while time.time() - t < timeout:
        try:
            if pred():
                return True
        except Exception:
            pass
        await asyncio.sleep(step)
    return False


class _Dash:
    """The real dashboard over HTTP, as the browser uses it (one-time token -> cookie, X-Jarves header)."""

    def __init__(self, url: str) -> None:
        import httpx
        from urllib.parse import urlsplit
        u = urlsplit(url)
        self.c = httpx.Client(base_url=f"{u.scheme}://{u.netloc}", timeout=15, follow_redirects=False)
        r = self.c.get("/?" + u.query)
        if r.status_code != 302:
            raise RuntimeError(f"dashboard token exchange: HTTP {r.status_code}")

    def get(self, path: str):
        r = self.c.get(path)
        return r.status_code, r.json()

    def post(self, path: str, body: dict):
        r = self.c.post(path, json=body, headers={"X-Jarves": "1"})
        return r.status_code, r.json()


def _db_rows(home: Path, sql: str, args=()) -> list[dict]:
    import sqlite3
    con = sqlite3.connect(home / "memory.db", timeout=10)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def _pattern():
    import numpy as np
    px = np.zeros((240, 320, 3), np.uint8)
    for i, c in enumerate([(192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0), (192, 0, 192)]):
        px[:, i * 64:(i + 1) * 64] = c
    return px


async def _boot_v2(home: Path):
    sys.path.insert(0, str(CORE))
    os.environ["JARVES_HOME"] = str(home)
    os.environ.pop("OPENAI_API_KEY", None)
    import jarves.app as app_mod
    from jarves.app import Core
    from jarves.camera.backends import FakeCamera, Probe
    from jarves.config import load_config
    from jarves.voice.audio import SimAudio
    from tools.suite_voice import FaceClient, LogTail
    app_mod.StreamingAudio = VMic
    cfg, _ = load_config()
    core = Core(cfg, home, audio=SimAudio(), console_logs=False, use_real_audio=True, stt=_NoSTT(), tts=_NoTTS())
    core.open_url = None                                       # never open a browser
    await core.start()
    svc = core.camera                                          # VIRTUAL camera: the Core's FakeCamera test pattern
    svc._probe_fn = lambda: Probe(True, "fake", "virtual camera (generated test pattern)")
    svc._factory = lambda p: FakeCamera([_pattern()])
    svc.detector, svc._models = None, None
    svc.probe(fresh=True)
    svc._report()
    core.features.invalidate()
    face = FaceClient(home, V2_PORT)
    await face.start()
    return core, face, LogTail(home / "logs" / "jarves.log")


def _priv(face, since: float = 0.0) -> list[dict]:
    return face.of("privacy.state", since)


async def _child_v2a(home: Path) -> None:
    core, face, log = await _boot_v2(home)
    t0 = time.time()
    url = await core.open_dashboard()
    dash = _Dash(url)
    aget = lambda p: asyncio.to_thread(dash.get, p)                 # noqa: E731
    apost = lambda p, b: asyncio.to_thread(dash.post, p, b)         # noqa: E731
    cli = lambda *a: asyncio.to_thread(jarves, list(a), home, 120)  # noqa: E731

    # ---- scheduler: jobs first (they need wall-clock time to come due)
    sc, parsed = await apost("/api/schedule/parse", {"when": "כל יום ב-8"})
    _, j1 = await apost("/api/jobs", {"when": "בעוד דקה", "task": "לשתות מים", "name": "qa-due", "action": "reminder"})
    await asyncio.sleep(2.5)
    draft_runs = _db_rows(home, "SELECT * FROM job_runs WHERE job_id=?", (j1.get("id"),))
    _, a1 = await apost(f"/api/jobs/{j1.get('id')}", {"op": "approve"})
    _, j3 = await apost("/api/jobs", {"when": "בעוד 4 דקות", "task": "קום", "name": "qa-catchup", "action": "reminder"})
    _, a3 = await apost(f"/api/jobs/{j3.get('id')}", {"op": "approve"})
    _, j2 = await apost("/api/jobs", {"when": "כל יום ב-23:59", "task": "לסגור חלונות", "name": "qa-manual",
                                      "action": "reminder"})
    _, a2 = await apost(f"/api/jobs/{j2.get('id')}", {"op": "approve"})
    t_rn = time.time()
    await apost(f"/api/jobs/{j2.get('id')}", {"op": "run_now"})

    # ---- V2-01 privacy strip at idle
    got = await _until(lambda: bool(_priv(face)), 3)
    await _until(lambda: _priv(face)[-1]["mic"]["state"] == "wake_only", 8)
    st = _priv(face)[-1] if _priv(face) else {}
    t_hb = time.time()
    await asyncio.sleep(11)
    beats = len(_priv(face, t_hb))
    snap = core.snapshot().get("privacy") or {}
    ok = (got and st.get("v") == 1 and st.get("mic", {}).get("state") == "wake_only"
          and st.get("cloud_audio") == {"on": False, "state": "off"} and st.get("speaker", {}).get("state") == "quiet"
          and st.get("camera", {}).get("state") == "disabled" and beats >= 2 and snap.get("mic") == st.get("mic"))
    _emit("V01 privacy strip at idle: truthful state, heartbeat, same as /status", ok,
          f"first msg {got}; mic {st.get('mic', {}).get('state')}, camera {st.get('camera', {}).get('state')}, cloud "
          f"{st.get('cloud_audio')}, speaker {st.get('speaker', {}).get('state')}; {beats} msgs in 11 s idle")

    # ---- V2-02 software mute from the strip
    tm = time.time()
    await face.send("mic.set_mute", {"muted": True})
    muted = await _until(lambda: _priv(face, tm) and _priv(face, tm)[-1]["mic"]["state"] == "muted", 2)
    await asyncio.sleep(1.0)
    mics_muted = VMic.open_mics
    tu = time.time()
    await face.send("mic.set_mute", {"muted": False})
    back = await _until(lambda: _priv(face, tu) and _priv(face, tu)[-1]["mic"]["state"] == "wake_only", 8)
    _emit("V02 privacy strip mute: muted shown within 2 s, every mic closed, unmute -> wake_only", muted and
          mics_muted == 0 and back, f"muted shown {muted}, open virtual mics while muted {mics_muted}, back {back}")

    # ---- V2-03 features on/off across CLI, dashboard and the running Core
    reg = core.registry
    notes_tool = next((n for n, (sk, tl) in reg._tools.items() if core.features.owner(sk.name, n) == "notes"), None)
    r_off = await cli("features", "off", "notes")
    seen_off = await _until(lambda: not core.features.enabled("notes"), 4)
    res_off = await reg.call(notes_tool, {}, origin="model") if notes_tool else None
    _, fl = await aget("/api/features")
    row = next((f for f in fl.get("features", []) if f["id"] == "notes"), {})
    sc_on, on = await apost("/api/features/notes", {"enabled": True})
    seen_on = await _until(lambda: core.features.enabled("notes"), 4)
    res_on = await reg.call(notes_tool, {}, origin="model") if notes_tool else None
    r_bad = await cli("features", "off", "no_such_feature")
    r_list = await cli("features", "list")
    ok = (r_off.returncode == 0 and seen_off and res_off is not None and res_off.code == "FEATURE_OFF"
          and row.get("enabled") is False and row.get("changed_by") == "cli" and sc_on == 200 and seen_on
          and res_on is not None and res_on.code != "FEATURE_OFF" and r_bad.returncode != 0
          and "Traceback" not in r_bad.stderr and "notes" in r_list.stdout)
    _emit("V03 features: CLI off -> Core refuses the tool (FEATURE_OFF); dashboard shows it; dashboard on -> tool "
          "runs; unknown id refused", ok,
          f"tool {notes_tool}; cli off rc {r_off.returncode}, core saw off {seen_off}, call -> "
          f"{getattr(res_off, 'code', None)}; dashboard row enabled {row.get('enabled')} by {row.get('changed_by')}; "
          f"dashboard on {sc_on}, core saw on {seen_on}, call -> {getattr(res_on, 'code', None)}; unknown id rc "
          f"{r_bad.returncode}")

    # ---- V2-04 camera preview with the virtual camera
    rows, tc = [], time.time()
    await face.send("camera.preview", {"open": True, "seconds": 3})
    await _until(lambda: face.of("camera.preview_state", tc), 15)
    pv0 = (face.of("camera.preview_state", tc) or [{}])[-1]
    rows.append(f"preview while camera off -> {pv0.get('reason')}")
    face.confirm_policy = "deny"
    td = time.time()
    await face.send("camera.set_enabled", {"on": True})
    await _until(lambda: face.of("camera.enabled", td), 8)
    den = (face.of("camera.enabled", td) or [{}])[-1]
    asked_deny = bool(face.of("confirm.request", td))
    requested = bool(core.features.status("camera").get("requested"))
    face.confirm_policy = "approve"
    ta = time.time()
    await face.send("camera.set_enabled", {"on": True})
    await _until(lambda: face.of("camera.enabled", ta), 8)
    acc = (face.of("camera.enabled", ta) or [{}])[-1]
    rows.append(f"enable: denied -> on {den.get('on')} (asked {asked_deny}, requested {requested}); approved -> on "
                f"{acc.get('on')} active {acc.get('active')} {acc.get('reason')}")
    cam_off = await _until(lambda: _priv(face)[-1]["camera"]["state"] == "off", 4)
    tp = time.time()
    # Event-driven (a 3 s window + fixed sleep(4.5) lost frames / the open event on a loaded machine, V04 in the parallel
    # run): a 12 s window, wait for the events (upper bounds only), then for the natural timeout close.
    await face.send("camera.preview", {"open": True, "seconds": 12})
    opened = await _until(lambda: any(p.get("open") for p in face.of("camera.preview_state", tp)), 20)
    in_prev = await _until(lambda: _priv(face, tp) and _priv(face, tp)[-1]["camera"]["state"] == "preview", 20)
    await _until(lambda: len(face.of("camera.preview_frame", tp)) >= 2, 25)
    await _until(lambda: any(p.get("open") is False for p in face.of("camera.preview_state", tp)), 30)
    frames = face.of("camera.preview_frame", tp)
    closed = [p for p in face.of("camera.preview_state", tp) if p.get("open") is False]
    ind = [p.get("on") for p in face.of("camera.indicator", tp)]
    after = await _until(lambda: _priv(face)[-1]["camera"]["state"] == "off", 15)
    rows.append(f"preview: open {opened}, strip 'preview' {in_prev}, frames {len(frames)} "
                f"({frames[0].get('width') if frames else '-'}x{frames[0].get('height') if frames else '-'}), closed "
                f"{[p.get('reason') for p in closed]}, indicator {ind}, strip back to off {after}")
    await cli("features", "off", "camera_preview")
    core.features.poll()
    tq = time.time()
    await face.send("camera.preview", {"open": True, "seconds": 3})
    await _until(lambda: face.of("camera.preview_state", tq), 3)
    pvq = (face.of("camera.preview_state", tq) or [{}])[-1]
    await cli("features", "on", "camera_preview")
    rows.append(f"preview switch off -> {pvq.get('reason')}")
    tf = time.time()
    await face.send("camera.set_enabled", {"on": False})
    await _until(lambda: face.of("camera.enabled", tf), 4)
    off = (face.of("camera.enabled", tf) or [{}])[-1]
    disabled = await _until(lambda: _priv(face)[-1]["camera"]["state"] == "disabled", 4)
    images = [p.name for p in home.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp")]
    rows.append(f"camera off -> on {off.get('on')}, strip disabled {disabled}; image files in home {images[:3]}")
    ok = (pv0.get("open") is False and pv0.get("reason") == "CAMERA_DISABLED" and asked_deny and den.get("on") is False
          and requested and acc.get("on") is True and cam_off and opened and in_prev and len(frames) >= 2
          and any(p.get("reason") == "timeout" for p in closed) and True in ind and ind[-1] is False and after
          and pvq.get("reason") == "PREVIEW_DISABLED" and off.get("on") is False and disabled and not images)
    _emit("V04 camera preview (virtual camera): refused while off, approval on screen, frames + strip 'preview', "
          "timeout close, switch gates, nothing on disk", ok, "; ".join(rows))

    # ---- V2-05 calendar guardrails with Google NOT connected (no fake Google)
    rows = []
    cr = await reg.call("calendar_create_event", {"title": "QA", "day": "tomorrow", "start": "10:00"}, origin="model")
    rows.append(f"model calendar_create_event -> {cr.code}")
    help_ = await reg.call("google_connect_help", {}, origin="model")
    rows.append(f"google_connect_help -> ok {help_.ok} {help_.code}")
    cw = core.features.status("calendar_write")
    rows.append(f"calendar_write default enabled {cw['enabled']} active {cw['active']}")
    r_on = await cli("features", "on", "calendar_write")
    cw_on = core.features.status("calendar_write")
    rows.append(f"cli on calendar_write rc {r_on.returncode} -> enabled {cw_on['enabled']} active {cw_on['active']} "
                f"({cw_on['reason'][:60]})")
    r_cal = await cli("calendar", "create", "--title", "QA", "--day", "tomorrow", "--start", "10:00")
    r_find = await cli("calendar", "find", "--day", "today")
    await cli("features", "reset", "calendar_write")
    rows.append(f"cli calendar create rc {r_cal.returncode} {(r_cal.stdout + r_cal.stderr).strip()[-90:]!r}; find rc "
                f"{r_find.returncode}")
    from jarves.skills.google import CAL_API, GoogleAuthError, calendar_guard
    guard = {}
    for label, (m, path, body) in {"DELETE event": ("DELETE", "/calendars/primary/events/x", None),
                                   "cancel via PATCH": ("PATCH", "/calendars/primary/events/x", {"status": "cancelled"}),
                                   "move": ("POST", "/calendars/primary/events/x/move", {}),
                                   "ACL change": ("POST", "/calendars/primary/acl", {"role": "owner"}),
                                   "create (allowed)": ("POST", "/calendars/primary/events", {"summary": "x"})}.items():
        try:
            calendar_guard(m, CAL_API + path, body)
            guard[label] = "allowed"
        except GoogleAuthError as e:
            guard[label] = e.code
    rows.append(f"guard {guard}")
    google_req = [e for e in log.refresh() if "googleapis" in json.dumps(e)]
    conds = {"model tool unknown": cr.code == "TOOL_UNKNOWN", "connect help honest": (not help_.ok and help_.code.startswith("GOOGLE_")),
             "default off": cw["enabled"] is False, "stays inactive": cw_on["active"] is False,
             "cli create fails": r_cal.returncode != 0, "no traceback": "Traceback" not in r_cal.stderr + r_find.stderr,
             "create allowed": guard["create (allowed)"] == "allowed",
             "delete/cancel/ACL refused": all(v != "allowed" for k, v in guard.items() if k != "create (allowed)"),
             "no googleapis call": not google_req}
    ok = all(conds.values())
    rows.append("failed: " + (", ".join(k for k, v in conds.items() if not v) or "-"))      # triage: which one, not just FAIL
    _emit("V05 calendar guardrails, Google not connected: no calendar tool, honest connect help, calendar_write "
          "stays inactive, CLI fails cleanly, delete/cancel/ACL refused", ok, "; ".join(rows))

    # ---- V2-06 scheduler: draft is inert, manual run, feature gate, due run on time
    rows = []
    manual = await _until(lambda: _db_rows(home, "SELECT state FROM job_runs WHERE job_id=? AND state='done'",
                                           (j2.get("id"),)), 40)
    if a1.get("status") != "active":
        rows.append(f"approve qa-due answered {json.dumps(a1, ensure_ascii=False)[:200]} (job {json.dumps(j1, ensure_ascii=False)[:160]})")
    rows.append(f"parse 'כל יום ב-8' -> {sc} {parsed.get('spec')}; draft runs before approval {len(draft_runs)}; "
                f"approved {a1.get('status')}/{a2.get('status')}/{a3.get('status')}; run_now done {bool(manual)} "
                f"started {round(_db_rows(home, 'SELECT MIN(started) s FROM job_runs WHERE job_id=?', (j2.get('id'),))[0]['s'] - t_rn, 1) if manual else '-'} s after the click")
    await cli("features", "off", "scheduler")
    core.features.poll()
    n_before = len(_db_rows(home, "SELECT id FROM job_runs WHERE job_id=?", (j2.get("id"),)))
    await apost(f"/api/jobs/{j2.get('id')}", {"op": "run_now"})
    await asyncio.sleep(35)                                      # > POLL_S: a held run would have started
    n_off = len(_db_rows(home, "SELECT id FROM job_runs WHERE job_id=?", (j2.get("id"),)))
    await cli("features", "on", "scheduler")
    t_sched_on = time.time()                 # qa-due may come due while the scheduler is switched off (35 s)
    core.features.poll()
    core.ops.scheduler.poke() if getattr(core, "ops", None) else None
    resumed = await _until(lambda: len(_db_rows(home, "SELECT id FROM job_runs WHERE job_id=?", (j2.get("id"),)))
                           > n_off, 40)
    rows.append(f"scheduler off: run_now ran {n_off - n_before} time(s) in 35 s; on again -> ran {resumed}")
    due_at = a1.get("next_run")
    on_time = await _until(lambda: _db_rows(home, "SELECT * FROM job_runs WHERE job_id=? AND state='done'",
                                            (j1.get("id"),)), max(5.0, (due_at or time.time()) - time.time() + 40), 0.5)
    run1 = (_db_rows(home, "SELECT * FROM job_runs WHERE job_id=?", (j1.get("id"),)) or [{}])[0]
    # late = after it was due AND the scheduler was on again (a held run must start right after switching on)
    lateness = round(run1.get("started", 0) - max(due_at or 0, t_sched_on), 1) if run1 else None
    alerts = _db_rows(home, "SELECT title, status, reason, delivered_via FROM alerts WHERE source='scheduler'")
    popups = [p.get("text", "")[:40] for p in face.of("assistant.text") if p.get("code") == "ALERT"]
    reports = _db_rows(home, "SELECT job_id, status FROM reports")
    rows.append(f"due job ran {bool(on_time)} trigger {run1.get('trigger')} {lateness} s after due; reports "
                f"{len(reports)}; alerts {[(a['status'], a['delivered_via'] or a['reason']) for a in alerts][:4]}; face "
                f"popups {len(popups)}")
    ok = (sc == 200 and parsed.get("spec") == "0 8 * * *" and not draft_runs and a1.get("status") == "active"
          and manual and n_off == n_before and resumed and on_time and run1.get("trigger") == "schedule"
          and lateness is not None and lateness < 35 and len(reports) >= 2
          and all(a["status"] in ("delivered", "suppressed", "pending") for a in alerts) and alerts)
    _emit("V06 scheduler via dashboard: parse, draft inert until approved, run_now, scheduler switch off holds runs, "
          "due reminder runs on time with report + alert", ok, "; ".join(rows))
    j3n = (_db_rows(home, "SELECT next_run FROM jobs WHERE id=?", (j3.get("id"),)) or [{}])[0].get("next_run")
    print("V2STATE " + json.dumps({"j1": j1.get("id"), "j2": j2.get("id"), "j3": j3.get("id"), "j3_next": j3n,
                                   "stopped_at": time.time(), "elapsed": round(time.time() - t0)}), flush=True)
    await face.stop()
    await core.stop()


async def _child_v2b(home: Path, state: dict) -> None:
    core, face, log = await _boot_v2(home)
    got = await _until(lambda: bool(_priv(face)), 3)
    j1, j2, j3 = state["j1"], state["j2"], state["j3"]
    caught = await _until(lambda: _db_rows(home, "SELECT * FROM job_runs WHERE job_id=? AND state='done'", (j3,)), 40,
                          0.5)
    run3 = (_db_rows(home, "SELECT * FROM job_runs WHERE job_id=?", (j3,)) or [{}])[0]
    n1 = len(_db_rows(home, "SELECT id FROM job_runs WHERE job_id=?", (j1,)))
    job3 = (_db_rows(home, "SELECT status, last_outcome FROM jobs WHERE id=?", (j3,)) or [{}])[0]
    rep3 = _db_rows(home, "SELECT title, late FROM reports WHERE job_id=?", (j3,))
    ok = (got and caught and run3.get("trigger") == "catch_up" and run3.get("late") == 1 and n1 == 1
          and job3.get("status") == "done" and rep3 and rep3[0]["late"] == 1)
    _emit("V07 scheduler across a Core restart: job due while JARVES was off runs once at start-up, labelled late; "
          "the earlier job is not repeated", ok,
          f"privacy.state at reconnect {got}; missed job ran {bool(caught)} trigger {run3.get('trigger')} late "
          f"{run3.get('late')} ({round(run3.get('started', 0) - state['j3_next'], 1) if run3 else '-'} s after due); "
          f"earlier job runs {n1}; job {job3}; report late {[r['late'] for r in rep3]}")
    tm = time.time()
    await face.stop()
    await core.stop()
    stopped = bool(log.codes("CORE_STOPPED"))
    _emit("V08 V2 Core stops cleanly after the feature run (CORE_STOPPED, port freed)", stopped and port_open_free(),
          f"CORE_STOPPED {stopped} after {time.time() - tm:.1f} s, port free {port_open_free()}")


def port_open_free(port: int = V2_PORT) -> bool:
    import socket
    s = socket.socket()
    try:
        return s.connect_ex(("127.0.0.1", port)) != 0
    finally:
        s.close()


def s14_v2_features():
    """SW-V2 features against a real Core: privacy strip, features on/off, camera preview, calendar guardrails,
    scheduler (incl. a real stop/start for the catch-up run)."""
    home = Path(tempfile.mkdtemp(prefix="sys-v2-"))
    write_cfg(home, V2_CFG)
    names = []

    def collect(out: str) -> dict:
        state = {}
        for line in out.splitlines():
            if line.startswith("V2 "):
                d = json.loads(line[3:])
                names.append(d["name"])
                rec(d["name"], d["ok"], d["detail"])
            elif line.startswith("V2STATE "):
                state = json.loads(line[8:])
        return state

    r = run(["python", "tools/suite_system.py", "--child", "v2a", "--home", str(home)], home, 600)
    state = collect(r.stdout)
    if not state:
        rec("V0x SW-V2 feature run (phase 1)", False, (r.stderr or r.stdout)[-300:])
        return
    late_s = 130                     # > scheduler.LATE_GRACE_S (120 s): the catch-up run must be labelled late
    wait = (state.get("j3_next") or time.time()) + late_s - time.time()
    if time.time() > (state.get("j3_next") or 0):
        rec("V07 scheduler across a Core restart", False, "harness: phase 1 ended after the job was due")
        return
    print(f"    Core stopped; waiting {wait:.0f} s until the next job is overdue ...", flush=True)
    time.sleep(wait)
    (home / "v2state.json").write_text(json.dumps(state), encoding="utf-8")
    r = run(["python", "tools/suite_system.py", "--child", "v2b", "--home", str(home)], home, 300)
    collect(r.stdout)
    if not any(n.startswith("V07") for n in names):
        rec("V07 scheduler across a Core restart (phase 2)", False, (r.stderr or r.stdout)[-300:])


def child_main(kind: str, home: Path) -> None:
    import traceback
    try:
        if kind == "v2a":
            asyncio.run(_child_v2a(home))
        else:
            asyncio.run(_child_v2b(home, json.loads((home / "v2state.json").read_text(encoding="utf-8"))))
    except Exception as e:
        _emit(f"V00 child {kind} crashed", False, f"{type(e).__name__}: {e} | " +
              " / ".join(traceback.format_exc().splitlines()[-4:]))


def write_report(started: float) -> Path:
    out = REPO / "docs" / "reviews" / "PRE_USE_TEST_REPORT_SYSTEM.md"
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    lines = ["# Pre-use test report: system (silent, headless)", "",
             f"Run: {time.strftime('%d %b %Y %H:%M')} on this PC. {passed}/{len(RESULTS)} passed. Duration {int(time.time() - started)} s.",
             "", "| Test | Result | Detail |", "|---|---|---|"]
    for n, ok, d in RESULTS:
        lines.append(f"| {n} | {'PASS' if ok else '**FAIL**'} | {d.replace('|', '/')[:200]} |")
    lines += ["", "Run it again: `uv --directory core run --no-sync python tools/suite_system.py`."]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pytest", action="store_true")
    ap.add_argument("--soak-min", type=float, default=10)
    ap.add_argument("--no-plan", action="store_true")
    ap.add_argument("--v2-only", action="store_true", help="only the SW-V2 feature checks (V01-V08)")
    ap.add_argument("--no-v2", action="store_true")
    ap.add_argument("--child", help=argparse.SUPPRESS)
    ap.add_argument("--home", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        child_main(a.child, Path(a.home))
        return
    t0 = time.time()
    if a.v2_only:
        s14_v2_features()
        print(f"\n{sum(1 for _, ok, _ in RESULTS if ok)}/{len(RESULTS)} passed (V2 only; report not rewritten)")
        return
    if not a.skip_pytest:
        s01_pytest()
    s02_godot()
    s04_doctor()
    s05_cli_smoke()
    s07_config_errors()
    s08_start_stop_cycles()
    s10_diag_masks_content()
    s11_log_rotation()
    s12_chatgpt_plan_text(a.no_plan)
    if a.soak_min > 0:
        s13_soak(a.soak_min)
    if not a.no_v2:
        s14_v2_features()
    s09_secret_scan()
    p = write_report(t0)
    print(f"\n{sum(1 for _, ok, _ in RESULTS if ok)}/{len(RESULTS)} passed -> {p}")


if __name__ == "__main__":
    main()
