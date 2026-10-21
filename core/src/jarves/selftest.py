"""Self-test: JARVES checks his own installation and says what is broken (owner request 21 Oct 2026).

    from jarves import selftest
    result = selftest.run()              # quick, ~10 s, read-only, no network, nothing spoken or recorded
    result = selftest.run(deep=True)     # + a Core start/stop in a temp home, wake model load, TTS/STT/device checks
    result = selftest.run(deep=True, audio=True)   # + one short quiet tone and a 0.5 s microphone level (never kept)

Returns a `SelfTestResult` (dataclasses; `as_dict()` is the JSON the dashboard and the self-management skill read).
Quick checks: configuration, database integrity, file permissions, connection probes of the features, tool registry /
guides / CLI-parity consistency, dashboard locale completeness (EN + HE), log privacy (a secret in the log is a FAIL),
disk space, version and update status. The Self-Diagnostics 2.0 start-up checks (diag.preflight) are reused for the
configuration, disk, audio and wake-model items. Every check is injectable (`checks=`), so tests plant faults.

Statuses: pass | warn | fail | skip (with the reason). A crashing check is a warn, never an exception.
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import logs

PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"
ORDER = {PASS: 0, SKIP: 0, WARN: 1, FAIL: 2}
LOG_TAIL_BYTES = 400_000
DB_CHECK_MAX_MB = 150          # quick mode skips integrity checks of bigger databases (deep mode runs them)
CORE_READY_TIMEOUT_S = 90.0


@dataclass
class Check:
    id: str
    title: str
    title_he: str
    status: str = PASS
    detail: str = ""
    fix: str = ""
    ms: float = 0.0
    deep: bool = False


@dataclass
class SelfTestResult:
    ts: float
    deep: bool
    audio: bool
    ms: float
    checks: list[Check] = field(default_factory=list)
    trigger: str = "manual"

    @property
    def status(self) -> str:
        worst = max((ORDER[c.status] for c in self.checks), default=0)
        return {0: PASS, 1: WARN, 2: FAIL}[worst]

    def count(self, status: str) -> int:
        return sum(1 for c in self.checks if c.status == status)

    @property
    def problems(self) -> list[Check]:
        return [c for c in self.checks if c.status in (WARN, FAIL)]

    def summary(self, lang: str = "en") -> str:
        """One short sentence (spoken) - the details are on the dashboard Health & speed page."""
        f, w, n = self.count(FAIL), self.count(WARN), len(self.checks)
        if lang == "he":
            if f:
                return f"בבדיקה העצמית נכשלו {f} מתוך {n} בדיקות; הראשונה: {self.problems[0].title_he}."
            if w:
                return f"הבדיקה העצמית עברה עם {w} אזהרות; הראשונה: {self.problems[0].title_he}."
            return f"הבדיקה העצמית עברה: כל {n} הבדיקות תקינות."
        if f:
            return f"The self-test failed {f} of {n} checks; first: {self.problems[0].title}."
        if w:
            return f"The self-test passed with {w} warning(s); first: {self.problems[0].title}."
        return f"The self-test passed: all {n} checks are fine."

    def as_dict(self) -> dict:
        return {"kind": "selftest", "ts": self.ts, "deep": self.deep, "audio": self.audio, "ms": self.ms,
                "trigger": self.trigger, "status": self.status,
                "counts": {s: self.count(s) for s in (PASS, WARN, FAIL, SKIP)},
                "checks": [asdict(c) for c in self.checks]}


@dataclass
class Ctx:
    """What the checks read. Everything outside the state folder is replaceable (planted faults in tests)."""
    home: Path
    cfg: object = None
    features: object = None
    registry: object = None
    deep: bool = False
    audio: bool = False
    clock: Callable[[], float] = time.time
    spawn_core: Callable[[Path], dict] | None = None       # deep: start + stop a Core in a temp home -> {ready_s, stop_s}
    audio_devices: Callable[[], tuple[int, int]] | None = None
    audio_roundtrip: Callable[[], dict] | None = None      # --audio only: {"played": bool, "mic_rms": float}
    update_status: Callable[[], dict] | None = None
    disk_free_gb: Callable[[Path], float] | None = None


# ---------------------------------------------------------------------- helpers
_PF = {"green": PASS, "amber": WARN, "red": FAIL, "skipped": SKIP}


def _preflight(c: Ctx, name: str) -> tuple[str, str, str]:
    from .diag import preflight as pf
    pc = pf.Ctx(home=c.home, cfg=c.cfg, config_path=Path(c.home) / "config.toml", features=c.features, clock=c.clock,
                audio_devices=c.audio_devices, disk_free_gb=c.disk_free_gb)
    status, detail, fix = getattr(pf, name)(pc)
    return _PF.get(status, WARN), detail, fix


def _tail(path: Path, limit: int = LOG_TAIL_BYTES) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if size > limit else lines


# ---------------------------------------------------------------------- the checks
def check_config(c: Ctx) -> tuple[str, str, str]:
    return _preflight(c, "_config")


def check_databases(c: Ctx) -> tuple[str, str, str]:
    bad, skipped, seen = [], [], 0
    for name in ("store.db", "memory.db"):
        p = Path(c.home) / name
        if not p.is_file():
            continue
        if not c.deep and p.stat().st_size > DB_CHECK_MAX_MB * 2**20:
            skipped.append(name)
            continue
        seen += 1
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
            try:
                res = con.execute("PRAGMA quick_check(1)").fetchone()
            finally:
                con.close()
            if not res or res[0] != "ok":
                bad.append(name)
        except sqlite3.Error:
            bad.append(name)
    if bad:
        return FAIL, f"integrity check failed: {', '.join(bad)}", "Restore the last backup (Backup page, or `jarvis restore`)."
    if skipped:
        return WARN, f"{', '.join(skipped)} too large for the quick check", "Run `jarvis selftest --deep`."
    if not seen:
        return SKIP, "no database yet (first start)", ""
    return PASS, f"{seen} database(s) intact", ""


def check_permissions(c: Ctx) -> tuple[str, str, str]:
    home = Path(c.home)
    if not home.is_dir():
        return FAIL, "the state folder does not exist", "Start JARVES once, or set JARVES_HOME."
    if not os.access(home, os.W_OK | os.X_OK):
        return FAIL, "the state folder is not writable", "Check the folder permissions of the JARVES data folder."
    if os.name == "posix":
        loose = []
        for rel in ("secrets.env", "google/token.json", "link_token"):
            p = home / rel
            if p.is_file() and p.stat().st_mode & 0o077:
                loose.append(rel)
        if loose:
            return WARN, f"readable by other users: {', '.join(loose)}", "chmod 600 on those files."
    return PASS, "state folder writable; secret files not shared", ""


def check_connections(c: Ctx) -> tuple[str, str, str]:
    f = c.features
    if f is None:
        from .features import Features
        f = Features(c.cfg, c.home, components=lambda: {}, skills=lambda: set())
    rows = f.list()
    on = [r for r in rows if r.get("enabled")]
    down = [r["id"] for r in on if not r.get("available") and r.get("check") != "checking"]
    if down:
        return WARN, f"{len(down)} of {len(on)} enabled features unavailable: {', '.join(down[:6])}", \
            "Open the Features page: each one names its missing piece."
    return PASS, f"{len(on)} enabled features, all probes ok", ""


def check_registry_and_guides(c: Ctx) -> tuple[str, str, str]:
    from . import cli_parity, dashboard, features as feat, guides
    for name in dashboard.PLUGIN_MODULES:
        try:
            importlib.import_module(name)
        except Exception:
            return FAIL, f"module {name} does not import", "Reinstall JARVES (`uv sync`)."
    try:
        from .setup_wizard import load_addons
        load_addons()
    except Exception:
        pass
    problems = []
    owners: dict[str, int] = {}
    gs = guides.load()
    for g in gs.values():
        for fid in g.features:
            owners[fid] = owners.get(fid, 0) + 1
        problems += [f"guide {g.id} links to unknown {ln}" for ln in g.links if ln not in gs]
    problems += [f"feature {fid} has no guide" for fid in feat.CATALOG if fid not in owners]
    problems += [f"feature {fid} has {n} guides" for fid, n in owners.items() if n > 1]
    problems += [f"feature {fid} has no CLI / dashboard marker" for fid in feat.CATALOG if fid not in cli_parity.FEATURES]
    reg = c.registry
    if reg is not None:
        names: set[str] = set()
        for skill in getattr(reg, "skills", {}).values():
            for t in skill.tools():
                if not (t.description or "").strip():
                    problems.append(f"tool {t.name} has no description")
                if t.name in names:
                    problems.append(f"tool {t.name} is registered twice")
                names.add(t.name)
    if problems:
        return WARN, f"{len(problems)} inconsistenc(ies): {'; '.join(problems[:4])}", \
            "Add the missing guide (core/src/jarves/guides/data) or the CLI parity marker."
    return PASS, f"{len(feat.CATALOG)} features, {len(gs)} guides, parity tables in step", ""


def _catalog(code: str) -> dict | None:
    p = Path(__file__).with_name("dashboard_static") / "locales" / f"{code}.js"
    try:
        src = p.read_text(encoding="utf-8").replace("\r\n", "\n")
        return json.loads(src.split("=\n", 1)[1].strip().rstrip(";"))
    except (OSError, IndexError, ValueError):
        return None


def check_locales(c: Ctx) -> tuple[str, str, str]:
    en, he = _catalog("en"), _catalog("he")
    if en is None or he is None:
        return FAIL, "a dashboard language catalog (en.js / he.js) is missing or unreadable", "Reinstall JARVES."
    ed, hd = en["dict"], he["dict"]
    missing = [k for k in ed if k not in hd]
    empty = [k for k, v in hd.items() if not str(v).strip()]
    if missing or empty:
        sample = (missing or empty)[0][:50]
        return FAIL, f"Hebrew misses {len(missing)} text(s), {len(empty)} empty (first: {sample!r})", \
            "Add them with core/tools/catalog_add.py."
    return PASS, f"EN {len(ed)} texts, HE {len(hd)}: complete", ""


def check_log_privacy(c: Ctx) -> tuple[str, str, str]:
    lines = _tail(Path(c.home) / "logs" / "jarves.log")
    if not lines:
        return SKIP, "no log yet", ""
    secrets = sum(1 for ln in lines if logs.has_secret(ln))
    leaks = sum(1 for ln in lines if logs.redact(ln) != ln)
    if secrets:
        return FAIL, f"{secrets} log line(s) hold a credential-shaped string", \
            "Do not share the log. `jarvis diag` redacts a bundle; report the log code so the field gets masked."
    if leaks:
        return WARN, f"{leaks} log line(s) hold something the redactor would mask", "Share logs only through `jarvis diag`."
    return PASS, f"{len(lines)} recent log lines clean", ""


def check_disk(c: Ctx) -> tuple[str, str, str]:
    return _preflight(c, "_disk")


def check_version(c: Ctx) -> tuple[str, str, str]:
    from . import updates
    try:
        st = (c.update_status or (lambda: updates.UpdateChecker(c.home, cfg=c.cfg).status()))()
    except Exception as e:
        return WARN, f"update status unreadable ({type(e).__name__})", ""
    ver = updates.version_line()
    if st.get("error"):
        return PASS, f"{ver}; last update check: {str(st['error'])[:80]}", ""
    if st.get("available"):
        rel = (st.get("latest") or {}).get("version", "?")
        return WARN, f"{ver}; version {rel} is available", "`jarvis update install` (asks first, keeps your data)."
    return PASS, f"{ver}; up to date (checked {st.get('checked_at') or 'never'})", ""


# ---- deep
def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def spawn_core(tmp: Path) -> dict:
    """Start the real background Core in `tmp` (own port, voice off, no keys, no microphone), wait for CORE_READY in
    its log, stop it. {ready_s, stop_s}; raises RuntimeError with the reason otherwise."""
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "setup_done").write_text("selftest", encoding="utf-8")
    (tmp / "config.toml").write_text(f"[link]\nport = {_free_port()}\n[voice]\nmode = \"off\"\nwake_enabled = false\n",
                                     encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY")}
    env.update(JARVES_HOME=str(tmp), PYTHONIOENCODING="utf-8")
    env.pop("JARVES_PROFILE", None)                  # the temp home is the default profile
    env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH", "")])
    exe = [sys.executable, "-m", "jarves.cli"]
    log = tmp / "logs" / "jarves.log"
    t0 = time.perf_counter()
    p = subprocess.Popen(exe + ["run", "--background"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    ready = None
    try:
        while time.perf_counter() - t0 < CORE_READY_TIMEOUT_S and p.poll() is None and ready is None:
            try:
                if '"CORE_READY"' in log.read_text(encoding="utf-8", errors="replace"):
                    ready = time.perf_counter() - t0
                    break
            except OSError:
                pass
            time.sleep(0.1)
        if ready is None:
            err = p.stderr.read().decode("utf-8", "replace")[-300:] if p.poll() is not None else "timeout"
            raise RuntimeError(logs.redact(err.strip() or "exited before ready"))
    finally:
        ts = time.perf_counter()
        subprocess.run(exe + ["stop"], env=env, capture_output=True, timeout=60)
        try:
            p.wait(30)
        except subprocess.TimeoutExpired:
            p.kill()
    return {"ready_s": round(ready, 2), "stop_s": round(time.perf_counter() - ts, 2)}


def check_core_smoke(c: Ctx) -> tuple[str, str, str]:
    tmp = Path(tempfile.mkdtemp(prefix="jarves-selftest-"))
    try:
        r = (c.spawn_core or spawn_core)(tmp)
    except Exception as e:
        return FAIL, f"the Core did not start in a temp home: {str(e)[:160]}", "Run `jarvis status` and `jarvis logs --errors`."
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return PASS, f"started in {r['ready_s']} s, stopped in {r['stop_s']} s", ""


def check_wake_model(c: Ctx) -> tuple[str, str, str]:
    v = getattr(c.cfg, "voice", None)
    if v is None or not getattr(v, "wake_enabled", True) or getattr(v, "mode", "") == "off":
        return SKIP, "wake word off", ""
    from .voice.wakeword import make_wake_detector
    ok, detail = make_wake_detector(v).check()          # loads the model; no audio is opened
    return (PASS, detail[:120], "") if ok else (FAIL, detail[:160], "uv sync --extra voice, or `jarvis wake train --revert`.")


def check_audio_devices(c: Ctx) -> tuple[str, str, str]:
    return _preflight(c, "_audio")


def check_speech(c: Ctx) -> tuple[str, str, str]:
    """TTS and STT readiness without speaking or recording: engine probes only (no cloud call, nothing billed)."""
    import importlib.util
    notes, bad = [], []
    try:
        from .voice.tts import tts_chain
        vc = c.cfg.voice
        engines = tts_chain(vc, allow_cloud=False)
        usable = [(e.name, e.check()) for e in engines]
        ok = [n for n, (good, _) in usable if good]
        notes.append("local speech: " + (", ".join(ok) if ok else "none"))
        if not ok and engines:
            bad.append("no local speech engine works (the cloud voice still does)")
    except Exception as e:
        bad.append(f"speech engines unreadable ({type(e).__name__})")
    if importlib.util.find_spec("faster_whisper") is None:
        notes.append("offline speech recognition not installed")
    else:
        notes.append("offline speech recognition installed")
    if bad:
        return WARN, "; ".join(bad + notes), "Windows: Settings > Time & language > Speech; or uv sync --extra voice."
    return PASS, "; ".join(notes), ""


def audio_roundtrip() -> dict:
    """--audio: one 0.2 s quiet tone and a 0.5 s microphone level; the samples are dropped at once."""
    import numpy as np
    import sounddevice as sd
    sr = 16000
    t = np.arange(int(sr * 0.2)) / sr
    sd.play((0.05 * np.sin(2 * np.pi * 660 * t)).astype("float32"), sr)
    sd.wait()
    rec = sd.rec(int(sr * 0.5), samplerate=sr, channels=1, dtype="float32")
    sd.wait()
    return {"played": True, "mic_rms": float(np.sqrt(np.mean(np.square(rec))))}


def check_audio_live(c: Ctx) -> tuple[str, str, str]:
    if not c.audio:
        return SKIP, "not requested (add --audio: one quiet tone and a 0.5 s level reading)", ""
    try:
        r = (c.audio_roundtrip or audio_roundtrip)()
    except Exception as e:
        return FAIL, f"audio round trip failed ({type(e).__name__})", "Check the microphone and speaker in Windows Sound settings."
    if r.get("mic_rms", 0.0) <= 0.0:
        return WARN, "the microphone delivered pure silence (muted or the wrong device?)", "Check the input device and its mute switch."
    return PASS, f"tone played; microphone level {r['mic_rms']:.4f}", ""


CHECKS: tuple[tuple[str, str, str, bool, Callable[[Ctx], tuple[str, str, str]]], ...] = (
    ("config", "Configuration", "הגדרות", False, check_config),
    ("databases", "Database integrity", "שלמות מסדי הנתונים", False, check_databases),
    ("permissions", "File permissions", "הרשאות קבצים", False, check_permissions),
    ("connections", "Connections (feature probes)", "חיבורים (בדיקות יכולות)", False, check_connections),
    ("registry_guides", "Tools, guides and CLI consistency", "עקביות כלים, מדריכים ושורת פקודה", False,
     check_registry_and_guides),
    ("locales", "Dashboard languages EN + HE", "שפות הלוח: אנגלית ועברית", False, check_locales),
    ("log_privacy", "Log privacy", "פרטיות היומן", False, check_log_privacy),
    ("disk", "Disk space", "מקום בדיסק", False, check_disk),
    ("version", "Version and updates", "גרסה ועדכונים", False, check_version),
    ("core_smoke", "Core start and stop (temp home)", "הפעלה וכיבוי של הליבה (תיקייה זמנית)", True, check_core_smoke),
    ("wake_model", "Wake model loads", "מודל מילת ההפעלה נטען", True, check_wake_model),
    ("audio_devices", "Microphone and speaker present", "מיקרופון ורמקול קיימים", True, check_audio_devices),
    ("speech", "Speech output and input ready", "דיבור והקשבה מוכנים", True, check_speech),
    ("audio_live", "Audio round trip", "בדיקת שמע חיה", True, check_audio_live),
)


def run(deep: bool = False, *, audio: bool = False, home: Path | None = None, cfg=None, features=None, registry=None,
        trigger: str = "manual", record: bool = True, clock: Callable[[], float] = time.time,
        checks=None, **probes) -> SelfTestResult:
    """Run the self-test. `record` appends one small row to the history (selfspeed.History) after the run."""
    if home is None:
        from .config import state_dir
        home = state_dir()
    if cfg is None:
        from .config import Config
        cfg = Config()
    c = Ctx(home=Path(home), cfg=cfg, features=features, registry=registry, deep=deep or audio, audio=audio, clock=clock,
            **probes)
    t0 = time.perf_counter()
    out: list[Check] = []
    for cid, title, title_he, is_deep, fn in (checks or CHECKS):
        if is_deep and not c.deep:
            continue
        t = time.perf_counter()
        try:
            status, detail, fix = fn(c)
        except Exception as e:                 # a broken check is reported, never raised
            status, detail, fix = WARN, f"the check itself failed ({type(e).__name__})", ""
        out.append(Check(cid, title, title_he, status, logs.redact(str(detail))[:240], fix, round((time.perf_counter() - t) * 1000, 1),
                         is_deep))
    res = SelfTestResult(ts=clock(), deep=c.deep, audio=audio, ms=round((time.perf_counter() - t0) * 1000, 1),
                         checks=out, trigger=trigger)
    logs.info("selfspeed", "SELFTEST_DONE", "self-test finished", status=res.status, passed=res.count(PASS),
              failed=res.count(FAIL), ms=res.ms, trigger=trigger)
    if record:
        from . import selfspeed
        selfspeed.History(Path(home), clock=clock).append("selftest", res.as_dict())
    return res
