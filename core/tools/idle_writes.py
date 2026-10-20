"""SW-V4 idle-write tracer: which files does the idle background Core write? (silent, offline, nothing billable)

    python tools/idle_writes.py [--idle-s 60] [--warmup-s 20] [--scenario combined] [--json out.json]

Starts the real Core in a throw-away JARVES_HOME like bench_startup, waits for CORE_READY + warm-up, then for the idle
window polls size+mtime of every file under the home twice a second and reads the process I/O counters once a second.
Prints a per-file table (changes seen, bytes grown, final size) and the process write rate.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_startup as bs  # noqa: E402


def snapshot(home: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for p in home.rglob("*"):
        try:
            if p.is_file():
                st = p.stat()
                out[p.relative_to(home).as_posix()] = (st.st_size, st.st_mtime_ns)
        except OSError:
            pass
    return out


def run(scenario: str = "combined", idle_s: float = 60, warmup_s: float = 20, port: int = 18766) -> dict:
    home = Path(tempfile.mkdtemp(prefix="jarves-idlew-"))
    (home / "setup_done").write_text("bench", encoding="utf-8")
    (home / "config.toml").write_text(f"[link]\nport = {port}\n" + bs.CONFIGS[scenario], encoding="utf-8")
    env = bs._env(home, scenario)
    log = home / "logs" / "jarves.log"
    p = subprocess.Popen([sys.executable, "-m", "jarves.cli", "run", "--background"], env=env, cwd=str(bs.CORE),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0, pid = time.perf_counter(), p.pid
    while True:
        if time.perf_counter() - t0 > 120 or p.poll() is not None:
            p.kill()
            raise SystemExit("Core did not become ready")
        if pid == p.pid:
            pid = bs.target(p.pid)
        try:
            if '"CORE_READY"' in log.read_text(encoding="utf-8", errors="replace"):
                break
        except OSError:
            pass
        time.sleep(0.1)
    time.sleep(warmup_s)
    before = snapshot(home)
    first = bs.sample(pid)
    stats: dict[str, dict] = {}
    prev, end = before, time.perf_counter() + idle_s
    per_s = []
    last_w, tick = first["write_mb"], 0
    while time.perf_counter() < end:
        time.sleep(0.5)
        cur = snapshot(home)
        for f, (size, mt) in cur.items():
            old = prev.get(f)
            if old is None or old[1] != mt or old[0] != size:
                s = stats.setdefault(f, {"changes": 0, "grown": 0})
                s["changes"] += 1
                s["grown"] += max(0, size - (old[0] if old else 0))
        prev = cur
        tick += 1
        if tick % 2 == 0:
            s = bs.sample(pid)
            if s:
                per_s.append(round((s["write_mb"] - last_w) * 1024, 2))
                last_w = s["write_mb"]
    last = bs.sample(pid)
    subprocess.run([sys.executable, "-m", "jarves.cli", "stop"], env=env, cwd=str(bs.CORE), capture_output=True,
                   timeout=60)
    try:
        p.wait(30)
    except subprocess.TimeoutExpired:
        p.kill()
    for f, s in stats.items():
        s["size"] = prev.get(f, (0, 0))[0]
    return {"process_kb_per_min": round((last["write_mb"] - first["write_mb"]) * 1024 * 60 / idle_s, 1),
            "files": dict(sorted(stats.items(), key=lambda kv: -kv[1]["changes"])),
            "kb_per_s_series": per_s, "home": str(home)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle-s", type=float, default=60)
    ap.add_argument("--warmup-s", type=float, default=20)
    ap.add_argument("--scenario", default="combined")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    r = run(a.scenario, a.idle_s, a.warmup_s)
    print(f"process writes: {r['process_kb_per_min']} KB/min")
    print(f"{'file':<40} {'changes':>8} {'grown_B':>9} {'size_B':>9}")
    for f, s in r["files"].items():
        print(f"{f:<40} {s['changes']:>8} {s['grown']:>9} {s['size']:>9}")
    print("KB per second (nonzero):", [(i, v) for i, v in enumerate(r["kb_per_s_series"]) if v])
    if a.json:
        a.json.write_text(json.dumps(r, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
