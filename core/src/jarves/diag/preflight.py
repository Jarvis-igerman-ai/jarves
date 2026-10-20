"""Preflight: the start-up self-check, before CORE_READY, at most 1.5 s (performance budget, package M).

Items: config parse and sanity, state folder writable (the only FATAL item), disk free above 1 GB, ports free,
the Godot executable present, audio devices present (mic and speaker), wake model files and hashes, the PRESENCE
(never the value) of required secrets, Google sign-in expiring within 48 h, prerequisites of every ON feature,
schema version, the last shutdown clean or not, clock sanity.

Statuses: green | amber | red | skipped (with a reason; e.g. the time budget ran out for a slow probe). A red or
amber item degrades only its component (health) and says so; the start continues. The result goes to the monitor
(diag_preflight), the Health page and, for a red item, a tray popup. Every probe is injectable (planted-fault
tests: tests/test_preflight.py), and nothing here writes outside the state folder's own probe file.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import logs

BUDGET_S = 1.5
MIN_FREE_GB = 1.0
WARN_FREE_GB = 3.0
GOOGLE_WARN_S = 48 * 3600
GOOGLE_TESTING_REFRESH_S = 7 * 86400     # Google Testing-mode refresh tokens die after 7 days
CLOCK_BACK_S = 300.0


@dataclass
class Item:
    id: str
    title: str
    title_he: str
    component: str
    status: str = "green"
    detail: str = ""
    fix: str = ""
    fix_he: str = ""
    fatal: bool = False
    ms: float = 0.0
    action: dict | None = None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "title", "title_he", "component", "status", "detail", "fix",
                                              "fix_he", "fatal", "ms", "action")}


@dataclass
class Ctx:
    """What the checks read. Every probe can be replaced (planted faults)."""
    home: Path
    cfg: object = None
    config_path: Path | None = None
    features: object = None
    clock: Callable[[], float] = time.time
    ports: list[tuple[str, int, str]] = field(default_factory=list)       # (host, port, component)
    find_godot: Callable[[], Path | None] | None = None
    audio_devices: Callable[[], tuple[int, int]] | None = None            # (inputs, outputs)
    disk_free_gb: Callable[[Path], float] | None = None
    env: dict | None = None
    previous: dict | None = None                                          # health.json of the last run
    wake_hash_last: str | None = None
    need_godot: bool = True
    need_audio: bool = True


def previous_state(home: Path) -> dict | None:
    """The last run's health.json (read BEFORE this run's Health overwrites it)."""
    try:
        return json.loads((Path(home) / "health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------- the checks
def _config(c: Ctx) -> tuple[str, str, str]:
    if c.config_path is not None and Path(c.config_path).is_file():
        from ..config import ConfigError, load_config
        try:
            load_config(Path(c.config_path))
        except ConfigError as e:
            return "red", f"config.toml has an error: {str(e)[:120]}", "Fix the line named in the message, or " \
                "rename config.toml to start with the defaults; the Core runs on the defaults meanwhile."
    cfg = c.cfg
    if cfg is not None:
        bad = []
        v = getattr(cfg, "voice", None)
        if v is not None and not (0.05 <= float(getattr(v, "wake_threshold", 0.5)) <= 0.99):
            bad.append("voice.wake_threshold out of 0.05-0.99")
        ai = getattr(cfg, "ai", None)
        if ai is not None and int(getattr(ai, "daily_request_limit", 1) or 0) < 0:
            bad.append("ai.daily_request_limit negative")
        if bad:
            return "amber", "; ".join(bad), "Correct the value on the Settings page."
    return "green", "parsed", ""


def _state_dir(c: Ctx) -> tuple[str, str, str]:
    p = Path(c.home) / f".preflight-{os.getpid()}.tmp"
    try:
        Path(c.home).mkdir(parents=True, exist_ok=True)
        p.write_text("ok", encoding="utf-8")
        p.unlink()
        return "green", str(Path(c.home).name), ""
    except OSError as e:
        return "red", f"the state folder is not writable ({type(e).__name__})", \
            "Check the folder permissions / free space of %LOCALAPPDATA%\\jarves (or JARVES_HOME)."


def _disk(c: Ctx) -> tuple[str, str, str]:
    try:
        free = c.disk_free_gb(Path(c.home)) if c.disk_free_gb else shutil.disk_usage(c.home).free / 1e9
    except OSError:
        return "amber", "free space unknown", ""
    if free < MIN_FREE_GB:
        return "red", f"only {free:.2f} GB free", "Free disk space (at least 1 GB): logs, backups and caches need room."
    if free < WARN_FREE_GB:
        return "amber", f"{free:.1f} GB free", "Free some disk space soon."
    return "green", f"{free:.0f} GB free", ""


def port_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _ports(c: Ctx) -> tuple[str, str, str]:
    taken = [f"{h}:{p} ({comp})" for h, p, comp in c.ports if p and not port_free(h, p)]
    if taken:
        return "red", "port taken: " + ", ".join(taken), \
            "Another JARVES (or another program) uses the port: `jarvis stop`, or change link.port in config.toml."
    return "green", f"{len(c.ports)} port(s) free", ""


def _godot(c: Ctx) -> tuple[str, str, str]:
    if not c.need_godot:
        return "skipped", "not needed (no window in this mode)", ""
    if c.find_godot is not None:
        exe = c.find_godot()
    else:
        from ..runtime.viewer import find_godot, installed_face
        exe = installed_face() if installed_face().is_file() else find_godot(getattr(getattr(c.cfg, "runtime", None),
                                                                                         "viewer_exe", "") or "")
    if exe is None:
        return "red", "the face app (Godot) was not found", "Reinstall JARVES, or set GODOT_EXE to the Godot 4 executable."
    return "green", Path(exe).name, ""


def _default_audio() -> tuple[int, int]:
    import sounddevice as sd                     # noqa: PLC0415 (only on a real start)
    devs = sd.query_devices()
    return (sum(1 for d in devs if d.get("max_input_channels", 0) > 0),
            sum(1 for d in devs if d.get("max_output_channels", 0) > 0))


def _audio(c: Ctx) -> tuple[str, str, str]:
    if not c.need_audio:
        return "skipped", "not needed (voice off or silent run)", ""
    try:
        ins, outs = (c.audio_devices or _default_audio)()
    except Exception as e:
        return "red", f"audio devices could not be listed ({type(e).__name__})", "uv sync --extra voice; reconnect the devices."
    missing = [n for n, k in (("microphone", ins), ("speaker", outs)) if k <= 0]
    if missing:
        return "red", "no " + " and no ".join(missing), \
            "Connect a microphone / speaker (Windows Sound settings), then restart JARVES. The rest keeps working."
    return "green", f"{ins} input(s), {outs} output(s)", ""


def _wake(c: Ctx) -> tuple[str, str, str]:
    v = getattr(c.cfg, "voice", None)
    if v is None or not getattr(v, "wake_enabled", True):
        return "skipped", "wake word off", ""
    name = str(getattr(v, "wake_word", "hey_jarvis") or "hey_jarvis")
    if name.endswith((".npz", ".tflite", ".onnx")):
        p = Path(name)
        if not p.is_absolute():
            p = Path(c.home) / name
        if not p.is_file():
            return "red", f"wake model file missing: {p.name}", \
                "Train it again (`jarvis wake train ...`) or go back to Hey Jarvis (`jarvis wake train --revert`)."
        h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        if c.wake_hash_last and c.wake_hash_last != h:
            return "amber", f"{p.name} changed since the last start (sha256 {h})", "Expected after a new training."
        return "green", f"{p.name} sha256 {h}", ""
    try:
        import importlib.util
        spec = importlib.util.find_spec("pyopen_wakeword")
        if spec is None or not spec.submodule_search_locations:
            return "red", "the wake word package is missing", "uv sync --extra voice"
        f = Path(list(spec.submodule_search_locations)[0]) / "models" / f"{name}.tflite"
        if not f.is_file():
            return "red", f"built-in wake model {name}.tflite missing", "Reinstall JARVES (uv sync --extra voice)."
        return "green", f"{name}.tflite present", ""
    except (ImportError, ValueError):
        return "red", "the wake word package is missing", "uv sync --extra voice"


def _secrets(c: Ctx) -> tuple[str, str, str]:
    env = c.env if c.env is not None else os.environ
    ai = getattr(c.cfg, "ai", None)
    need = []
    if ai is not None and ai.provider == "openai" and ai.connection in ("api_key", "combined"):
        need.append("OPENAI_API_KEY")
    missing = [k for k in need if not str(env.get(k, "")).strip()]
    if missing:
        return "red", "not set (presence check only, never the value) - " + ", ".join(missing), \
            "Run `jarvis key` to save the OpenAI API key; until then voice/AI use the free local path."
    return "green", (", ".join(need) + " present") if need else "none required", ""


def _google(c: Ctx) -> tuple[str, str, str]:
    p = Path(c.home) / "google" / "token.json"
    try:
        t = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "skipped", "Google not connected", ""
    if t.get("needs_reconnect") and not t.get("refresh_token"):
        return "red", "the Google sign-in expired", "Run `jarvis google start` (reconnect Google)."
    if not t.get("refresh_token"):
        return "skipped", "Google not connected", ""
    exp = t.get("refresh_expires_at") or (float(t.get("connected_at") or 0) + GOOGLE_TESTING_REFRESH_S
                                          if t.get("connected_at") else None)
    if exp:
        left = float(exp) - c.clock()
        if left <= GOOGLE_WARN_S:
            return "amber", f"the Google sign-in expires in {max(0, left) / 3600:.0f} h", \
                "Reconnect Google now (`jarvis google start`) so calendar and mail keep working."
    return "green", "connected", ""


def _features(c: Ctx, deadline: float) -> tuple[str, str, str]:
    f = c.features
    if f is None:
        return "skipped", "features not loaded", ""
    bad, unchecked = [], 0
    from ..features import CATALOG
    for fid in sorted(CATALOG):
        try:
            if not f.enabled(fid):
                continue
            if time.perf_counter() > deadline:
                unchecked += 1
                continue
            ok, why = f.available(fid)
            if not ok:
                bad.append(f"{fid} ({why[:60]})")
        except Exception as e:
            bad.append(f"{fid} ({type(e).__name__})")
    if bad:
        return "amber", f"{len(bad)} switched-on feature(s) unavailable: " + "; ".join(bad[:4]), \
            "Open the Features page: each one says what is missing."
    if unchecked:
        return "skipped", f"{unchecked} feature(s) not checked (time budget)", ""
    return "green", "every ON feature has its prerequisites", ""


def _schema(c: Ctx) -> tuple[str, str, str]:
    """On-disk tables against the current schema (read-only; a missing column is migrated when the store opens)."""
    from .. import store as store_mod, userdata
    behind = []
    for fname, schema in (("store.db", store_mod.SCHEMA), ("memory.db", userdata.SCHEMA)):
        p = Path(c.home) / fname
        if not p.is_file():
            continue
        try:
            ref = sqlite3.connect(":memory:")
            ref.executescript(schema)
            want = {t: {r[1] for r in ref.execute(f"PRAGMA table_info({t})")}
                    for (t,) in ref.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            ref.close()
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=1)
            try:
                for t, cols in want.items():
                    have = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
                    if have and cols - have:
                        behind.append(f"{fname}:{t} lacks {', '.join(sorted(cols - have))}")
            finally:
                con.close()
        except sqlite3.Error as e:
            return "red", f"{fname} cannot be read ({type(e).__name__})", \
                "Restore the last backup (Backup page) or `jarvis restore`."
    if behind:
        return "amber", "schema behind: " + "; ".join(behind[:3]), "Migrated automatically on first use; if a page " \
            "fails, restore the last backup."
    return "green", "current", ""


def _shutdown(c: Ctx) -> tuple[str, str, str]:
    prev = c.previous
    if not prev:
        return "skipped", "first start (no earlier run)", ""
    if prev.get("core") == "stopped":
        return "green", "the last run stopped cleanly", ""
    return "amber", f"the last run did not stop cleanly (state {prev.get('core', '?')})", \
        "A crash, a forced close or a power cut. The self-check looks at the last log lines."


def _clock(c: Ctx) -> tuple[str, str, str]:
    now = c.clock()
    year = time.gmtime(now).tm_year
    if not 2025 <= year <= 2100:
        return "red", f"the system clock says {year}", "Fix the Windows date and time (Settings > Time)."
    last = float((c.previous or {}).get("ts") or 0)
    if last and now < last - CLOCK_BACK_S:
        return "red", f"the clock is {(last - now) / 60:.0f} min behind the last run", \
            "Fix the Windows date and time (sync now); schedules and sign-ins depend on it."
    return "green", "ok", ""


CHECKS = (
    ("config", "Configuration", "הגדרות", "config", False, _config),
    ("state_dir", "State folder writable", "תיקיית הנתונים ניתנת לכתיבה", "storage", True, _state_dir),
    ("disk", "Disk space", "מקום בדיסק", "storage", False, _disk),
    ("ports", "Ports free", "פורטים פנויים", "link", False, _ports),
    ("godot", "Face app (Godot) present", "אפליקציית הפנים קיימת", "link", False, _godot),
    ("audio", "Microphone and speaker present", "מיקרופון ורמקול קיימים", "audio", False, _audio),
    ("wake_model", "Wake model files", "קובצי מילת ההפעלה", "wake_word", False, _wake),
    ("secrets", "Required secrets present", "המפתחות הדרושים קיימים", "ai", False, _secrets),
    ("google_token", "Google sign-in", "ההתחברות לגוגל", "google", False, _google),
    ("features", "Feature prerequisites", "דרישות היכולות", "features", False, None),
    ("schema", "Database schema", "מבנה מסד הנתונים", "storage", False, _schema),
    ("shutdown", "Last shutdown", "הכיבוי האחרון", "core", False, _shutdown),
    ("clock", "Clock sanity", "שעון המערכת", "core", False, _clock),
)


def run(c: Ctx) -> dict:
    t0 = time.perf_counter()
    deadline = t0 + BUDGET_S * 0.8
    items: list[Item] = []
    for cid, title, title_he, comp, fatal, fn in CHECKS:
        t = time.perf_counter()
        try:
            status, detail, fix = _features(c, deadline) if cid == "features" else fn(c)
        except Exception as e:                 # a broken probe is reported, never raised
            status, detail, fix = "amber", f"check failed: {type(e).__name__}", ""
        it = Item(cid, title, title_he, comp, status, logs.redact(detail)[:200], fix, fix, fatal and status == "red",
                  round((time.perf_counter() - t) * 1000, 1))
        if cid == "google_token" and status in ("red", "amber"):
            it.action = {"type": "remedy", "remedy": "reconnect_google"}
        items.append(it)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    red = [i for i in items if i.status == "red"]
    return {"ts": c.clock(), "ms": ms, "ok": not red, "fatal": any(i.fatal for i in items),
            "within_budget": ms <= BUDGET_S * 1000, "items": [i.as_dict() for i in items]}


def save(store, result: dict) -> int:
    from . import db
    return db.write(store, "INSERT INTO diag_preflight (ts, ms, ok, fatal, items) VALUES (?,?,?,?,?)",
                    (result["ts"], result["ms"], int(result["ok"]), int(result["fatal"]), db.dumps(result["items"])))


def latest(store) -> dict | None:
    from . import db
    r = db.rows(store, "SELECT * FROM diag_preflight ORDER BY id DESC LIMIT 1")
    if not r:
        return None
    return {"ts": r[0]["ts"], "ms": r[0]["ms"], "ok": bool(r[0]["ok"]), "fatal": bool(r[0]["fatal"]),
            "items": db.j(r[0]["items"], [])}


def apply(health, result: dict) -> list[str]:
    """Degrade only the component of each RED item (the start continues); amber items are shown on the Health page
    only. A component the Core does not report (config, storage, features) is never invented here: the item is
    on the Health page and in the log. Returns the touched components."""
    touched = []
    for it in result["items"]:
        if it["status"] != "red" or health is None:
            continue
        comp = it["component"]
        cur = health.components.get(comp)
        if comp == "link":
            comp, cur = "face_link", health.components.get("face_link")
        if cur is None or cur.status in ("unavailable", "disabled"):
            continue
        health.set(comp, "degraded", f"preflight: {it['detail']}", it.get("fix", ""))
        touched.append(comp)
    return touched


# ---------------------------------------------------------------------- the Core hooks
def run_core(core) -> dict:
    """Called by Core.start() before the face link opens and before CORE_READY (budget 1.5 s)."""
    from . import db
    c = core.cfg
    # silent / test runs (no real audio) skip the feature probes: they are the slow part and need real devices
    ctx = Ctx(home=core.home, cfg=c, config_path=getattr(core, "config_path", None),
              features=core.features if core.use_real_audio else None,
              ports=[(c.link.host, c.link.port, "link")], previous=getattr(core, "_preflight_prev", None),
              need_godot=getattr(core, "runtime_mode", "") not in ("headless", "test") and core.use_real_audio,
              need_audio=core.use_real_audio and c.voice.mode != "off")
    if not core.use_real_audio:
        ctx.audio_devices = lambda: (1, 1)
    try:
        st = core.history.store if getattr(core, "history", None) is not None else None
        if st is not None:
            last = db.rows(st, "SELECT detail FROM diag_events WHERE kind='wake_hash' ORDER BY id DESC LIMIT 1")
            ctx.wake_hash_last = last[0]["detail"] if last else None
    except Exception:
        st = None
    res = run(ctx)
    logs.info("health", "PREFLIGHT_DONE", "preflight done", red=sum(i["status"] == "red" for i in res["items"]),
              amber=sum(i["status"] == "amber" for i in res["items"]), ms=res["ms"], ok=res["ok"],
              within_budget=res["within_budget"])
    for it in res["items"]:
        if it["status"] in ("red", "amber"):
            log = logs.warn if it["status"] == "red" else logs.info
            log("health", "PREFLIGHT_ITEM", "preflight item not green", item=it["id"],
                status=it["status"], part=it["component"])
    if st is not None:
        try:
            save(st, res)
            w = next((i for i in res["items"] if i["id"] == "wake_model"), None)
            if w and "sha256" in w["detail"]:
                db.write(st, "INSERT INTO diag_events (ts, kind, name, detail) VALUES (?,?,?,?)",
                         (res["ts"], "wake_hash", "wake_model", w["detail"].rsplit(" ", 1)[-1].strip(")")))
        except Exception:
            logs.warn("health", "PREFLIGHT_SAVE_FAILED", "preflight result not stored")
    return res


def finish_core(core) -> None:
    """Just before CORE_READY: degrade the components of red/amber items and raise one popup for red ones."""
    res = getattr(core, "preflight", None)
    if not res:
        return
    apply(core.health, res)
    red = [i for i in res["items"] if i["status"] == "red"]
    ops = getattr(core, "ops", None)
    if red and ops is not None and getattr(ops, "alerts", None) is not None:
        try:
            ops.alerts.raise_("health", "בדיקת הפעלה: " + ", ".join(i["title_he"] for i in red[:3]),
                              "; ".join(f"{i['title']}: {i['detail']}" for i in red[:3])[:400], priority="normal",
                              kind="health", channel="popup", dedupe_key="preflight:" + ",".join(i["id"] for i in red))
        except Exception:
            logs.warn("health", "PREFLIGHT_ALERT_FAILED", "preflight popup not raised")
