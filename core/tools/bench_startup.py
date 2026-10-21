"""SW-V3 P0.3 startup / idle benchmark: silent, headless, offline, no microphone, nothing billable.

    core/.venv/Scripts/python.exe core/tools/bench_startup.py [--runs 3] [--idle-s 60] [--scenario core|cloud]

Starts the REAL `jarves run --background` Core in a throw-away JARVES_HOME (own port, no secrets, wake word off so no
microphone opens), waits for CORE_READY in its log, samples CPU / RAM / threads / disk I/O while it idles, then stops
it with `jarves stop`. Scenario "cloud" sets a dummy OPENAI_API_KEY so the live-voice code path (prewarm of the cloud
voice libraries) runs exactly as on an installed PC, but no request is ever sent (no conversation is started).
Prints one JSON line per run plus a median summary. Windows (ctypes) and Linux (/proc) - no extra packages.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
SRC = CORE / "src"
sys.path.insert(0, str(SRC))

CONFIGS = {
    # Core only: no audio stack at all (the always-on skeleton: scheduler, health loop, link, tray, dashboard skill)
    "core": '[voice]\nmode = "off"\nwake_enabled = false\n',
    # Installed default path for an API-key user, minus the microphone: live voice selected, prewarm runs
    "cloud": '[ai]\nconnection = "api_key"\n[voice]\nmode = "live"\nwake_enabled = false\ngreeting = false\n',
    # The owner's installed choice: ChatGPT plan for typed answers + API key for GPT-Live (not signed in here)
    "combined": '[ai]\nconnection = "combined"\n[voice]\nmode = "live"\nwake_enabled = false\ngreeting = false\n',
}


from jarves.procstats import children, sample, target  # noqa: E402,F401  (moved to the package)


# ------------------------------------------------------------------ which files change while idle (diagnosis)
WARMUP_S = 20.0     # after CORE_READY: prewarm / first scheduler pass / first health heartbeat are start-up work


def snapshot_files(home: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for p in home.rglob("*"):
        try:
            if p.is_file():
                st = p.stat()
                out[p.relative_to(home).as_posix()] = (st.st_size, st.st_mtime_ns)
        except OSError:
            pass
    return out


def diff_files(prev: dict, cur: dict, counts: dict[str, int]) -> dict:
    for f, v in cur.items():
        if prev.get(f) != v:
            counts[f] = counts.get(f, 0) + 1
    return cur


# ------------------------------------------------------------------ one run
def _env(home: Path, scenario: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY")}
    # PYTHONDONTWRITEBYTECODE: on a fresh checkout the first import of each module compiles and WRITES its .pyc; that is
    # one-time warm-up work, not steady-state churn, and it would be counted as idle disk writes (CI Linux: 1340 KB/min).
    env.update(JARVES_HOME=str(home), PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    if scenario in ("cloud", "combined"):
        env["OPENAI_API_KEY"] = "sk-test-bench-dummy-never-sent"   # "test": the S09 secret scan skips fakes
    return env


def one_run(scenario: str, idle_s: float, port: int) -> dict:
    home = Path(tempfile.mkdtemp(prefix="jarves-bench-"))
    (home / "setup_done").write_text("bench", encoding="utf-8")
    (home / "config.toml").write_text(f"[link]\nport = {port}\n" + CONFIGS[scenario], encoding="utf-8")
    env = _env(home, scenario)
    log = home / "logs" / "jarves.log"
    t0 = time.perf_counter()
    p = subprocess.Popen([sys.executable, "-m", "jarves.cli", "run", "--background"], env=env, cwd=str(CORE),
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    ready_s = core_ms = None
    peak_start = 0.0
    pid = p.pid
    while time.perf_counter() - t0 < 120 and p.poll() is None:
        if pid == p.pid:
            pid = target(p.pid)
        s = sample(pid)
        if s:
            peak_start = max(peak_start, s["rss_mb"])
        try:
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                if '"CORE_READY"' in line:
                    ready_s, core_ms = time.perf_counter() - t0, json.loads(line).get("ms")
                    break
        except OSError:
            pass
        if ready_s:
            break
        time.sleep(0.05)
    if ready_s is None:
        err = p.stderr.read().decode("utf-8", "replace")[-800:] if p.poll() is not None else "timeout"
        p.kill()
        raise SystemExit(f"Core did not become ready: {err}")
    at_ready = sample(pid)
    # settle: background prewarm / first scheduler pass finish; then measure the steady idle window
    time.sleep(WARMUP_S)
    a = sample(pid)
    rss_series, t_idle = [], time.perf_counter()
    files_prev, file_changes = snapshot_files(home), {}
    while time.perf_counter() - t_idle < idle_s:
        time.sleep(1.0)
        s = sample(pid)
        if s:
            rss_series.append(s["rss_mb"])
        files_prev = diff_files(files_prev, snapshot_files(home), file_changes)
    b = sample(pid)
    kids = children(pid)
    wall = time.perf_counter() - t_idle
    ts = time.perf_counter()
    subprocess.run([sys.executable, "-m", "jarves.cli", "stop"], env=env, cwd=str(CORE), capture_output=True, timeout=60)
    try:
        p.wait(30)
    except subprocess.TimeoutExpired:
        p.kill()
    stop_s = time.perf_counter() - ts
    prewarm = None
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"PREWARM_DONE"' in line:
            prewarm = json.loads(line).get("ms")
    return {"scenario": scenario, "spawn_to_ready_s": round(ready_s, 2), "core_start_ms": core_ms,
            "prewarm_ms": prewarm, "rss_at_ready_mb": round(at_ready["rss_mb"], 1),
            "peak_rss_mb": round(max(b["peak_mb"], peak_start), 1), "steady_rss_mb": round(statistics.median(rss_series), 1),
            "rss_growth_mb": round(rss_series[-1] - rss_series[0], 1), "private_mb": round(b["private_mb"], 1),
            "idle_cpu_pct": round(100 * (b["cpu_s"] - a["cpu_s"]) / wall, 2),
            "startup_cpu_s": round(a["cpu_s"], 2),
            "idle_files": ", ".join(f"{f} x{n}" for f, n in sorted(file_changes.items(), key=lambda kv: -kv[1])),
            "idle_disk_write_kb_per_min": round((b["write_mb"] - a["write_mb"]) * 1024 * 60 / wall, 1),
            "idle_disk_read_kb_per_min": round((b["read_mb"] - a["read_mb"]) * 1024 * 60 / wall, 1),
            "threads": b["threads"], "child_processes": kids, "stop_s": round(stop_s, 2), "exit": p.returncode}


STAGES = {"CORE_READY": "ready", "LIVE_STARTED": "live_started", "LIVE_FIRST_AUDIO": "first_audio",
          "LINK_FACE_CONNECTED": "face", "PREWARM_DONE": "prewarm_done"}


def log_stages(path: Path, last: int) -> None:
    """Per-start stage timings (seconds after CORE_STARTING) from a real jarves.log; codes only, no transcript."""
    runs, cur = [], None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
            t = time.mktime(time.strptime(d["ts"][:19], "%Y-%m-%dT%H:%M:%S")) + float("0." + d["ts"][20:23])
        except (ValueError, KeyError):
            continue
        if d.get("code") == "CORE_STARTING":
            cur = {"start_utc": d["ts"][:19], "t0": t}
            runs.append(cur)
        elif cur is not None and d.get("code") in STAGES and STAGES[d["code"]] not in cur:
            cur[STAGES[d["code"]]] = round(t - cur["t0"], 1)
            if d["code"] == "PREWARM_DONE":
                cur["prewarm_ms"] = d.get("ms")
    for r in runs[-last:]:
        r.pop("t0")
        print(json.dumps(r))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--idle-s", type=float, default=60)
    ap.add_argument("--scenario", choices=sorted(CONFIGS), default="core")
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument("--log", type=Path, help="instead: print stage timings of the last starts in this jarves.log")
    ap.add_argument("--last", type=int, default=10)
    a = ap.parse_args()
    if a.log:
        log_stages(a.log, a.last)
        return 0
    rows = []
    for i in range(a.runs):
        r = one_run(a.scenario, a.idle_s, a.port)
        rows.append(r)
        print(json.dumps(r), flush=True)
    med = {k: statistics.median(r[k] for r in rows) for k, v in rows[0].items()
           if isinstance(v, (int, float)) and all(r[k] is not None for r in rows)}
    print("MEDIAN " + json.dumps({"scenario": a.scenario, "runs": a.runs, **med}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
