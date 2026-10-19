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
        rec("S12 ChatGPT-plan text path (real)", False, "not signed in: run jarvis chatgpt login")
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
    a = ap.parse_args()
    t0 = time.time()
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
    s09_secret_scan()
    p = write_report(t0)
    print(f"\n{sum(1 for _, ok, _ in RESULTS if ok)}/{len(RESULTS)} passed -> {p}")


if __name__ == "__main__":
    main()
