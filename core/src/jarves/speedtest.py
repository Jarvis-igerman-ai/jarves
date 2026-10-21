"""Speed test: how fast and how light is JARVES right now, and is that worse than before? (owner request 21 Oct 2026)

    from jarves import speedtest
    result = speedtest.run()             # ~15 s; read-only; nothing spoken, recorded or billed

Returns a `SpeedResult` (dataclasses; `as_dict()` is the JSON the dashboard and the self-management skill read). Every
metric is checked against its budget in core/perf_budgets.toml (class owner = strict, ci = loose; the CI gate runs this
same code with `--class ci --gate`) and against the history: more than 20 % worse than the median of the last 5 runs
is a warning. Measured, with the cheapest honest source for each:

- startup        CORE_STARTING -> CORE_READY of the newest start in the log (spawn_to_ready_s, core_start_ms, ...)
- idle           the running Core's process counters over a short window (CPU, RAM, disk reads / writes per minute)
- inproc         features list with an expired slow probe, session-start probe path, dashboard first page and APIs
- history        tool-call p50 / p95 from TOOL_RESULT log records; wake-to-first-audio and dead-air p95 from the
                 Self-Diagnostics monitor events (store.db) - "n/a" until there were enough real conversations
- net            one HEAD request to api.openai.com/v1/models (no key sent, no tokens, nothing billed)
- installer      the size of the newest installer (dist/build-info.json or a downloaded one)
- bench          (--bench, slow, needs a source checkout) a real Core in a temp home via tools/bench_startup.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import logs

PASS, WARN, FAIL, NA = "pass", "warn", "fail", "n/a"
WARN_FRACTION = 0.85            # within 15 % of the ceiling = warn
REGRESSION_PCT = 20.0           # worse than the median of the last 5 runs by more than this = warn
HISTORY_N = 5
MIN_SAMPLES = 5                 # percentiles from fewer real samples are not reported
HISTORY_DAYS = 14
BUDGETS_FILE = Path(__file__).resolve().parents[2] / "perf_budgets.toml"     # core/perf_budgets.toml (source checkout)

GROUPS: dict[str, tuple[str, ...]] = {
    "startup": ("spawn_to_ready_s", "core_start_ms", "live_started_s", "first_audio_s"),
    "idle": ("idle_cpu_pct", "steady_rss_mb", "private_mb", "threads", "idle_disk_write_kb_per_min",
             "idle_disk_read_kb_per_min"),
    "inproc": ("features_list_ms", "live_tools_probe_ms", "dashboard_first_load_ms", "dashboard_api_max_ms"),
    "history": ("tool_p50_ms", "tool_p95_ms", "wake_to_first_audio_s", "dead_air_p95_s"),
    "net": ("openai_roundtrip_ms",),
    "installer": ("installer_size_mb",),
}
BENCH_ONLY = ("child_processes", "rss_growth_mb", "stop_s")
UNITS = {"ms": "ms", "s": "s", "mb": "MB", "pct": "%", "kb_per_min": "KB/min", "threads": "count"}


@dataclass
class Metric:
    name: str
    value: float | None
    unit: str = ""
    status: str = NA
    max: float | None = None
    baseline: float | None = None
    prev_median: float | None = None
    trend_pct: float | None = None
    detail: str = ""


@dataclass
class SpeedResult:
    ts: float
    cls: str
    ms: float
    metrics: list[Metric] = field(default_factory=list)
    trigger: str = "manual"

    @property
    def status(self) -> str:
        sts = {m.status for m in self.metrics}
        return FAIL if FAIL in sts else WARN if WARN in sts else PASS

    def by_status(self, status: str) -> list[Metric]:
        return [m for m in self.metrics if m.status == status]

    def summary(self, lang: str = "en") -> str:
        """One short sentence (spoken) - the details are on the dashboard Health & speed page."""
        bad = self.by_status(FAIL) or self.by_status(WARN)
        if lang == "he":
            if bad:
                m = bad[0]
                kind = "חורג מהתקציב" if m.status == FAIL else "איטי יותר מהרגיל"
                return f"בדיקת המהירות: {m.name} {kind} ({_fmt(m.value)} {m.unit})."
            return "בדיקת המהירות עברה: הכול בתוך התקציב."
        if bad:
            m = bad[0]
            what = "is over its budget" if m.status == FAIL else "is slower than usual"
            return f"Speed test: {m.name} {what} ({_fmt(m.value)} {m.unit})."
        return "Speed test passed: everything is within budget."

    def as_dict(self) -> dict:
        return {"kind": "speedtest", "ts": self.ts, "class": self.cls, "ms": self.ms, "trigger": self.trigger,
                "status": self.status,
                "counts": {s: len(self.by_status(s)) for s in (PASS, WARN, FAIL, NA)},
                "metrics": [asdict(m) for m in self.metrics],
                "values": {m.name: m.value for m in self.metrics if m.value is not None}}


def _fmt(v) -> str:
    return "-" if v is None else f"{v:g}"


# ---------------------------------------------------------------------- budgets and judging
def load_budgets(path: Path | None = None) -> dict:
    """{metric: {unit, baseline, owner_max, ci_max}} from perf_budgets.toml ([metrics.*] and [installer.*]); {} when
    the file is not shipped (an installed build): values are then reported without a verdict."""
    try:
        raw = tomllib.loads(Path(path or BUDGETS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = dict(raw.get("metrics", {}))
    out.update(raw.get("installer", {}))
    return out


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (p in 0-100) of a non-empty list."""
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(-(-p / 100 * len(s) // 1)) - 1))
    return s[k]


def judge(name: str, value: float | None, budgets: dict, cls: str, previous: list[float]) -> Metric:
    b = budgets.get(name, {})
    mx = b.get(f"{cls}_max")
    m = Metric(name, None if value is None else round(float(value), 2), b.get("unit", ""), NA, mx, b.get("baseline"))
    if value is None:
        return m
    m.status = PASS
    if mx is not None:
        if value > mx:
            m.status, m.detail = FAIL, f"over the {cls} budget of {mx:g}"
        elif value > WARN_FRACTION * mx:
            m.status, m.detail = WARN, f"within {100 - WARN_FRACTION * 100:.0f} % of the {cls} budget of {mx:g}"
    prev = [p for p in previous[-HISTORY_N:] if p is not None]
    if prev:
        med = statistics.median(prev)
        m.prev_median = round(med, 2)
        if med > 0:
            m.trend_pct = round((value - med) / med * 100, 1)
            if m.trend_pct > REGRESSION_PCT and m.status == PASS:
                m.status, m.detail = WARN, f"{m.trend_pct:g} % worse than the median of the last {len(prev)} runs"
    return m


# ---------------------------------------------------------------------- measurers (each returns {metric: value})
def measure_startup(home: Path, **_) -> dict:
    """Newest start in the log: seconds from CORE_STARTING to CORE_READY / LIVE_STARTED / LIVE_FIRST_AUDIO."""
    from .logview import LogReader
    from .store import parse_ts
    t0 = None
    out: dict = {}
    stage = {"CORE_READY": "spawn_to_ready_s", "LIVE_STARTED": "live_started_s", "LIVE_FIRST_AUDIO": "first_audio_s"}
    for r in LogReader(home).records():
        code = r.get("code")
        if code == "CORE_STARTING":
            t0, out = parse_ts(r.get("ts")), {}
        elif t0 is not None and code in stage and stage[code] not in out:
            out[stage[code]] = round(parse_ts(r.get("ts")) - t0, 2)
            if code == "CORE_READY" and isinstance(r.get("ms"), (int, float)):
                out["core_start_ms"] = r["ms"]
    return out


def measure_idle(home: Path, *, idle_window_s: float = 10.0, sleep=time.sleep, pid: int | None = None, **_) -> dict:
    """The running Core's process counters over `idle_window_s` (CPU %, RAM, disk reads / writes per minute)."""
    from . import procstats
    from .runtime import lock
    if pid is None:
        holder = lock.read_holder(home) if lock.held(home) else None
        pid = (holder or {}).get("pid")
    if not pid:
        return {}
    pid = int(pid)                           # the lock holds the real Core pid (no launcher stub to follow)
    a = procstats.sample(pid)
    if not a:
        return {}
    t = time.perf_counter()
    sleep(idle_window_s)
    b = procstats.sample(pid)
    wall = max(time.perf_counter() - t, 1e-3)
    if not b:
        return {}
    return {"idle_cpu_pct": round(100 * (b["cpu_s"] - a["cpu_s"]) / wall, 2), "steady_rss_mb": round(b["rss_mb"], 1),
            "private_mb": round(b["private_mb"], 1), "threads": b["threads"],
            "idle_disk_write_kb_per_min": round((b["write_mb"] - a["write_mb"]) * 1024 * 60 / wall, 1),
            "idle_disk_read_kb_per_min": round((b["read_mb"] - a["read_mb"]) * 1024 * 60 / wall, 1)}


def _timed(fn) -> float:
    t = time.perf_counter()
    fn()
    return round((time.perf_counter() - t) * 1000, 1)


def measure_inproc(home: Path | None = None, *, repeats: int = 3, settle_s: float = 0.5, **_) -> dict:
    """In-process timings with a private JARVES_HOME. The music probe is made slow (0.3 s, as with Pear closed)."""
    from . import features as feat
    from .config import Config
    home = Path(tempfile.mkdtemp(prefix="jarves-gate-"))
    saved = os.environ.get("JARVES_HOME")
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
    try:
        out.update(measure_dashboard(home))
    finally:
        if saved is None:
            os.environ.pop("JARVES_HOME", None)
        else:
            os.environ["JARVES_HOME"] = saved
    return out


def measure_dashboard(home: Path) -> dict:
    import http.client

    from . import dashboard
    from .config import Config
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
                raise RuntimeError(f"dashboard {path} -> {r.status}")
            return round(ms, 1)
        first = get("/")
        apis = [get(f"/api/{p}") for p in ("overview", "sessions", "costs?days=30", "issues", "skills", "memory",
                                           "settings")]
        return {"dashboard_first_load_ms": first, "dashboard_api_max_ms": max(apis)}
    finally:
        d.stop()


def measure_history(home: Path, *, now: float | None = None, **_) -> dict:
    """Tool p50 / p95 from TOOL_RESULT log records; wake-to-first-audio and dead-air p95 from the monitor events."""
    import sqlite3

    from .logview import LogReader
    from .store import parse_ts
    now = time.time() if now is None else now
    since = now - HISTORY_DAYS * 86400
    out: dict = {}
    ms = [float(r["ms"]) for r in LogReader(home).records()
          if r.get("code") == "TOOL_RESULT" and isinstance(r.get("ms"), (int, float)) and parse_ts(r.get("ts")) >= since
          and not str(r.get("tool", "")).startswith(("run_selftest", "run_speedtest"))]
    if len(ms) >= MIN_SAMPLES:
        out["tool_p50_ms"], out["tool_p95_ms"] = percentile(ms, 50), percentile(ms, 95)
    p = home / "store.db"
    if p.is_file():
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=1)
            try:
                def vals(name):
                    return [r[0] for r in con.execute("SELECT value FROM diag_events WHERE kind='turn' AND name=? AND "
                                                      "ts>=? AND value IS NOT NULL", (name, since))]
                wake, wait = vals("wake_to_first_audio"), vals("reply_wait")
            finally:
                con.close()
            if len(wake) >= 3:
                out["wake_to_first_audio_s"] = round(statistics.median(wake), 2)
            if len(wait) >= MIN_SAMPLES:
                out["dead_air_p95_s"] = round(percentile(wait, 95), 2)
        except sqlite3.Error:
            pass
    return out


def _head(url: str, timeout: float) -> int:
    import httpx
    return httpx.head(url, timeout=timeout, follow_redirects=False).status_code


def measure_net(home: Path, *, http: Callable[[str, float], int] | None = None, **_) -> dict:
    """One HEAD request: connection + TLS + a response. No key is sent, no tokens are used, nothing is billed."""
    t = time.perf_counter()
    try:
        status = (http or _head)("https://api.openai.com/v1/models", 8.0)
    except Exception:
        return {}
    return {"openai_roundtrip_ms": round((time.perf_counter() - t) * 1000, 1)} if status < 500 else {}


def measure_installer(home: Path, **_) -> dict:
    """Size of the newest installer: dist/build-info.json of a source checkout, else one the updater downloaded."""
    cands = []
    info = Path(__file__).resolve().parents[3] / "dist" / "build-info.json"
    try:
        d = json.loads(info.read_text(encoding="utf-8-sig"))
        exe = info.parent / str(d["installer"])
        cands.append(exe.stat().st_size if exe.is_file() else int(d["size"]))
    except (OSError, ValueError, KeyError):
        pass
    for p in (Path(home) / "updates").glob("JARVES-Setup-*.exe"):
        try:
            cands.append(p.stat().st_size)
        except OSError:
            pass
    return {"installer_size_mb": round(cands[0] / 2**20, 1)} if cands else {}


def measure_bench(home: Path, *, idle_s: float = 30.0, **_) -> dict:
    """--bench: a real Core in a temp home (tools/bench_startup.py; source checkout only)."""
    tools = Path(__file__).resolve().parents[2] / "tools"
    if not (tools / "bench_startup.py").is_file():
        return {}
    sys.path.insert(0, str(tools))
    try:
        import bench_startup as bs
        r = bs.one_run("combined", idle_s, 18767)
    finally:
        sys.path.remove(str(tools))
    return {k: v for k, v in r.items() if isinstance(v, (int, float)) and v is not None}


MEASURERS: dict[str, Callable[..., dict]] = {
    "startup": measure_startup, "idle": measure_idle, "inproc": measure_inproc, "history": measure_history,
    "net": measure_net, "installer": measure_installer, "bench": measure_bench,
}


def run(*, home: Path | None = None, cls: str | None = None, bench: bool = False, groups: tuple[str, ...] | None = None,
        trigger: str = "manual", record: bool = True, clock: Callable[[], float] = time.time,
        budgets: dict | None = None, measurers: dict[str, Callable[..., dict]] | None = None,
        **opts) -> SpeedResult:
    """Run the speed test. `opts` reach the measurers (idle_window_s, http, sleep, pid, repeats, idle_s).
    `record` appends one small row to the history (selfspeed.History) after the run."""
    from . import selfspeed
    if home is None:
        from .config import state_dir
        home = state_dir()
    home = Path(home)
    cls = cls or ("ci" if os.environ.get("CI") else "owner")
    budgets = load_budgets() if budgets is None else budgets
    ms = {**MEASURERS, **(measurers or {})}
    names = list(groups or GROUPS) + (["bench"] if bench else [])
    hist = selfspeed.History(home, clock=clock)
    prev_rows = [r.get("values", {}) for r in hist.rows("speedtest")]
    t0 = time.perf_counter()
    values: dict = {}
    for g in names:
        try:
            got = ms[g](home, **opts)
        except Exception as e:                  # one broken measurer must not hide the others
            logs.warn("selfspeed", "SPEEDTEST_PART_FAILED", "a speed test part failed", kind=g, error_type=type(e).__name__)
            got = {}
        values.update(got)                      # bench (last) replaces the cheaper estimates of the same metric
    wanted = [n for g in names for n in GROUPS.get(g, ())]
    if bench:
        wanted += [n for n in budgets if n in BENCH_ONLY or n in values]
    seen: set[str] = set()
    rows = []
    for n in wanted + [n for n in values if n not in wanted]:
        if n in seen:
            continue
        seen.add(n)
        rows.append(judge(n, values.get(n), budgets, cls, [r.get(n) for r in prev_rows]))
    res = SpeedResult(ts=clock(), cls=cls, ms=round((time.perf_counter() - t0) * 1000, 1), metrics=rows, trigger=trigger)
    logs.info("selfspeed", "SPEEDTEST_DONE", "speed test finished", status=res.status, ms=res.ms, trigger=trigger,
              failed=len(res.by_status(FAIL)))
    if record:
        hist.append("speedtest", res.as_dict())
    return res
