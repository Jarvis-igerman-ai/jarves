"""SW-V4 M performance gate: fails (exit 1) when a measured value exceeds its budget in core/perf_budgets.toml.

    python tools/perf_gate.py [--class owner|ci] [--runs N] [--idle-s S] [--scenario combined]
                              [--results out.json] [--from-json measured.json] [--log jarves.log] [--installer-json m.json] [--propose]

Measures (silent, offline, nothing billable): the real background Core via tools/bench_startup.py (median of the runs)
plus in-process timings (features_list with an expired slow probe, the live-session probe path, the dashboard's first
page and API loads). `--from-json` skips measuring and checks given numbers (used by tests and for re-checks).
`--log` adds the stage timings of the newest start in a real jarves.log (owner PC).
`--propose` prints budget lines (baseline x1.5 owner, x3 ci) for a deliberate update - see docs/reviews/SWV4_PERF_BUDGETS.md.
`--class` defaults to ci when the CI environment variable is set, else owner.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import statistics
import sys
import tempfile
import time
import tomllib
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
BUDGETS = CORE / "perf_budgets.toml"
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def load_budgets(path: Path = BUDGETS) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def check(measured: dict, budgets: dict, cls: str) -> list[dict]:
    """One row per budgeted metric: {metric, value, max, unit, status}; status is ok | FAIL | n/a (not measured)."""
    key = f"{cls}_max"
    rows = []
    for name, b in budgets["metrics"].items():
        v, mx = measured.get(name), b[key]
        status = "n/a" if v is None else ("ok" if v <= mx else "FAIL")
        rows.append({"metric": name, "value": v, "max": mx, "unit": b.get("unit", ""), "status": status,
                     "baseline": b.get("baseline")})
    return rows


def failures(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["status"] == "FAIL"]


def propose(budgets: dict) -> str:
    out = []
    for name, b in budgets["metrics"].items():
        base = b.get("baseline") or 0
        out.append(f"[metrics.{name}] baseline={base} owner_max={round(base * 1.5, 2)} ci_max={round(base * 3, 2)}")
    return "\n".join(out)


# ------------------------------------------------------------------ measuring
def measure_bench(scenario: str, runs: int, idle_s: float, port: int) -> dict:
    import bench_startup as bs
    rows = [bs.one_run(scenario, idle_s, port) for _ in range(runs)]
    for i, r in enumerate(rows):          # diagnosis in the CI log: which files changed during the idle window
        print(f"run {i + 1}: idle write {r['idle_disk_write_kb_per_min']} KB/min; files changed: {r['idle_files'] or '-'}")
    return {k: statistics.median(r[k] for r in rows) for k, v in rows[0].items()
            if isinstance(v, (int, float)) and all(r[k] is not None for r in rows)}


def _timed(fn) -> float:
    t = time.perf_counter()
    fn()
    return round((time.perf_counter() - t) * 1000, 1)


def measure_inproc(repeats: int = 3, settle_s: float = 0.5) -> dict:
    """In-process timings with a private JARVES_HOME. The music probe is made slow (0.3 s, as with Pear closed)."""
    from jarves import features as feat
    from jarves.config import Config
    home = Path(tempfile.mkdtemp(prefix="jarves-gate-"))
    os.environ["JARVES_HOME"] = str(home)
    real = feat._tcp_open
    feat._tcp_open = lambda url, timeout=0.3: (time.sleep(0.3), False)[1]
    out: dict = {}
    try:
        f = feat.Features(Config(), home, components=lambda: {}, skills=lambda: set())
        f.warm("music")                                                    # first probe (synchronous), then expire
        lists, probes = [], []
        for _ in range(repeats):
            t, ok, why = f._probe_cache["music"]
            f._probe_cache["music"] = (t - feat.PROBE_TTL_S - 1, ok, why)
            lists.append(_timed(f.list))
            time.sleep(settle_s)                                           # let the background re-check finish
            t, ok, why = f._probe_cache["music"]
            f._probe_cache["music"] = (t - feat.PROBE_TTL_S - 1, ok, why)
            probes.append(_timed(lambda: f.available("music")))
            time.sleep(settle_s)
        out["features_list_ms"] = statistics.median(lists)
        out["live_tools_probe_ms"] = statistics.median(probes)
    finally:
        feat._tcp_open = real
    out.update(measure_dashboard(home))
    return out


def measure_dashboard(home: Path) -> dict:
    import http.client
    from jarves import dashboard
    from jarves.config import Config
    (home / "workspace").mkdir(exist_ok=True)
    d = dashboard.Dashboard(home, Config()).start()
    try:
        def get(path: str) -> float:
            c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
            t = time.perf_counter()
            c.request("GET", path, headers={"Host": f"127.0.0.1:{d.port}",
                                            "Cookie": f"{dashboard.COOKIE}={d.url.split('t=')[1]}"})
            r = c.getresponse()
            r.read()
            ms = (time.perf_counter() - t) * 1000
            c.close()
            if r.status >= 500:
                raise SystemExit(f"dashboard {path} -> {r.status}")
            return round(ms, 1)
        first = get("/")
        apis = [get(f"/api/{p}") for p in ("overview", "sessions", "costs?days=30", "issues", "skills", "memory",
                                           "settings")]
        return {"dashboard_first_load_ms": first, "dashboard_api_max_ms": max(apis)}
    finally:
        d.stop()


def measure_log(path: Path) -> dict:
    import bench_startup as bs
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bs.log_stages(path, 1)
    lines = [json.loads(x) for x in buf.getvalue().splitlines() if x.strip()]
    if not lines:
        return {}
    r = lines[-1]
    return {k: r[s] for k, s in (("live_started_s", "live_started"), ("first_audio_s", "first_audio")) if s in r}


def render(rows: list[dict], cls: str) -> str:
    w = max(len(r["metric"]) for r in rows)
    lines = [f"perf gate (class={cls})", f"{'metric':<{w}}  {'value':>10}  {'max':>8}  {'baseline':>9}  status"]
    for r in rows:
        v = "-" if r["value"] is None else r["value"]
        lines.append(f"{r['metric']:<{w}}  {v!s:>10}  {r['max']!s:>8}  {r['baseline']!s:>9}  {r['status']}  {r['unit']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--class", dest="cls", choices=("owner", "ci"), default=None)
    ap.add_argument("--runs", type=int)
    ap.add_argument("--idle-s", type=float)
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument("--results", type=Path, help="write measured values + rows as JSON")
    ap.add_argument("--from-json", type=Path, help="check these measured values instead of measuring")
    ap.add_argument("--log", type=Path, help="also check the newest start in this real jarves.log")
    ap.add_argument("--installer-json", type=Path, help="installer size/time numbers from packaging/installer_metrics.py "
                                                         "(SW-V4 L); checked against [installer.*] in perf_budgets.toml")
    ap.add_argument("--no-bench", action="store_true", help="skip the real-Core bench (in-process timings only)")
    ap.add_argument("--propose", action="store_true")
    a = ap.parse_args(argv)
    budgets = load_budgets()
    if a.propose:
        print(propose(budgets))
        return 0
    cls = a.cls or ("ci" if os.environ.get("CI") else "owner")
    meta = budgets["meta"]
    if a.from_json:
        measured = json.loads(a.from_json.read_text(encoding="utf-8"))
    else:
        measured = {}
        if not a.no_bench:
            runs = a.runs or meta["ci_runs" if cls == "ci" else "default_runs"]
            idle = a.idle_s or meta["ci_idle_s" if cls == "ci" else "default_idle_s"]
            measured.update(measure_bench(a.scenario or meta["scenario"], runs, idle, a.port))
        measured.update(measure_inproc())
        if a.log:
            measured.update(measure_log(a.log))
    rows = check(measured, budgets, cls)
    if a.installer_json:
        sys.path.insert(0, str(CORE.parent / "packaging"))
        import installer_metrics as im
        inst = json.loads(a.installer_json.read_text(encoding="utf-8"))
        rows += im.check(inst, budgets.get("installer", {}), cls)
        measured = {**measured, **{f"installer.{k}": v for k, v in inst.items()}}
    print(render(rows, cls))
    if a.results:
        a.results.write_text(json.dumps({"class": cls, "measured": measured, "rows": rows}, indent=1), encoding="utf-8")
    bad = failures(rows)
    if bad:
        print("PERF GATE FAILED: " + ", ".join(f"{r['metric']}={r['value']} > {r['max']}" for r in bad))
        return 1
    print("PERF GATE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
