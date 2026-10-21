"""Auto-remediation ONLY through approvals: a CLOSED list of repairs.

Each remedy has a risk level, a dry-run description (what will happen, en + he), a pre-check (is it possible and
useful now?), the action, and a post-check that verifies it worked. `run()` always asks the approvals service
first (popup / tray / face / dashboard / voice; risk medium) and records the request, the decision, the result
and the post-check in diag_remediations. A denial, a timeout or no channel does NOTHING.

Never automatic, also not with autonomy grants (autonomy grants are low risk only; these are medium). The one
exception (owner decision OD-37): "restart the face link" may be granted for automatic use with the setting
health.auto_face_link_restart; such a run is still recorded and marked automatic.

Core-level remedies need the running Core (link, wake loop, audio, dashboard, bridges, voice engine); home-level
ones (caches, feature setup, update rollback) also run from the CLI / a dashboard outside the Core.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .. import logs
from . import db


@dataclass
class RemedyCtx:
    home: Path
    store: object
    core: object = None                       # the running Core (None in the CLI / an external dashboard)
    features: object = None
    cfg: object = None
    ask: Callable[..., Awaitable[bool]] | None = None    # approvals: ask(prompt, title=, why=, risk=, kind=, details=)
    args: dict | None = None
    clock: Callable[[], float] = time.time


@dataclass
class Remedy:
    id: str
    title: str
    title_he: str
    dry_run: str
    dry_run_he: str
    needs_core: bool
    pre: Callable[[RemedyCtx], tuple[bool, str]]
    act: Callable[[RemedyCtx], object]        # sync or async; returns a short result text
    post: Callable[[RemedyCtx], tuple[bool, str]]
    risk: str = "medium"
    grantable: bool = False                   # may the owner allow it to run automatically (OD-37)?


async def _maybe(v):
    return await v if inspect.isawaitable(v) else v


def _comp(core, name: str) -> str:
    c = getattr(getattr(core, "health", None), "components", {}).get(name)
    return getattr(c, "status", "unknown") if c is not None else "unknown"


# ---------------------------------------------------------------------- the closed list
def _pre_core(attr: str):
    def f(c: RemedyCtx):
        if c.core is None:
            return False, "needs the running JARVES (start it, then use the Health page or ask by voice)"
        return (getattr(c.core, attr, None) is not None, f"no {attr} in this Core" if getattr(c.core, attr, None)
                is None else "ready")
    return f


async def _act_face_link(c: RemedyCtx):
    await c.core.link.stop()
    await c.core.link.start()
    return "face link restarted"


def _post_face_link(c: RemedyCtx):
    from .preflight import port_free
    link = c.core.link
    listening = getattr(link, "_server", None) is not None and not port_free(link.host, link.port)
    return listening, f"listening on {link.host}:{link.port}" if listening else "the link is not listening"


async def _act_wake(c: RemedyCtx):
    core = c.core
    for t in list(getattr(core, "_bg", [])):
        coro = t.get_coro() if hasattr(t, "get_coro") else None
        if coro is not None and getattr(coro, "__name__", "") == "_wake_loop" and not t.done():
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
    core.wake = None
    core._setup_wake()
    return "wake loop restarted"


def _post_wake(c: RemedyCtx):
    st = _comp(c.core, "wake_word")
    return (c.core.wake is not None and st == "ok") or st == "disabled", f"wake_word {st}"


def _act_audio(c: RemedyCtx):
    c.core.dev.check_audio()
    return "audio devices probed again"


def _post_audio(c: RemedyCtx):
    st = _comp(c.core, "audio")
    return st not in ("unavailable",), f"audio {st}"


async def _act_dashboard(c: RemedyCtx):
    core = c.core
    d = getattr(core, "_dashboard", None)
    if d is not None and getattr(d, "running", False):
        await asyncio.to_thread(d.stop)
    from ..dashboard import Dashboard
    core._dashboard = await asyncio.to_thread(lambda: Dashboard(core.home, core.cfg, workspace=core.workspace,
                                                                memory=core.memory, settings=core.settings).start())
    ops = getattr(core, "ops", None)
    if ops is not None:
        core._dashboard.ctx._scheduler = ops.scheduler
    return f"dashboard restarted on port {core._dashboard.port}"


def _post_dashboard(c: RemedyCtx):
    d = getattr(c.core, "_dashboard", None)
    return bool(d is not None and getattr(d, "running", False)), "dashboard running" if d else "no dashboard"


BRIDGES = ("agents_bridge", "whatsapp_bridge")


def _pre_bridges(c: RemedyCtx):
    if c.core is None:
        return False, "needs the running JARVES"
    have = [b for b in BRIDGES if getattr(getattr(c.core, b, None), "restart", None)]
    return (bool(have), ", ".join(have) if have else "no bridge is installed (packages E / F)")


async def _act_bridges(c: RemedyCtx):
    done = []
    for b in BRIDGES:
        br = getattr(c.core, b, None)
        if br is not None and getattr(br, "restart", None):
            await _maybe(br.restart())
            done.append(b)
    return "restarted " + ", ".join(done)


def _post_bridges(c: RemedyCtx):
    bad = [b for b in BRIDGES if getattr(c.core, b, None) is not None and _comp(c.core, b.replace("_bridge", "")) ==
           "unavailable"]
    return not bad, "ok" if not bad else "still down: " + ", ".join(bad)


def _speech_files(c: RemedyCtx) -> list[Path]:
    d = Path(c.home) / "cache"
    return sorted(d.glob("speech_*.wav")) if d.is_dir() else []


def _act_speech(c: RemedyCtx):
    n = 0
    for p in _speech_files(c):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return f"{n} cached speech file(s) removed"


def _pre_pear(c: RemedyCtx):
    return (c.features is not None, "ready" if c.features is not None else "features not loaded")


def _act_pear(c: RemedyCtx):
    c.features.invalidate("music")
    return "Pear probe cache cleared"


def _post_pear(c: RemedyCtx):
    return "music" not in getattr(c.features, "_probe_cache", {}), "cache cleared"


def _act_guides(c: RemedyCtx):
    from .. import guides
    guides._cache.update(key=None, guides={})
    return "guide cache cleared"


def _post_guides(c: RemedyCtx):
    from .. import guides
    return guides._cache.get("key") is None or bool(guides.load()), f"{len(guides.load())} guide(s) load"


async def _act_google(c: RemedyCtx):
    if c.core is not None and getattr(c.core, "open_dashboard", None):
        await c.core.open_dashboard("#/guide/google")
        return "the Google reconnect guide is open (sign in there)"
    return "run: jarvis google start"


def _post_google(c: RemedyCtx):
    return True, "the reconnect flow was opened; the sign-in itself is yours"


def _pre_local_voice(c: RemedyCtx):
    if c.core is None:
        return False, "needs the running JARVES"
    if getattr(c.core, "voice_mode", "") not in ("live", "realtime"):
        return False, f"already on {getattr(c.core, 'voice_mode', '?')}"
    if getattr(c.core, "live", None) is not None:
        return False, "a conversation is open; try again after it ends"
    return True, "ready"


def _act_local_voice(c: RemedyCtx):
    c.core.cfg.voice.mode = "push_to_talk"
    c.core._select_voice_mode()
    return "voice switched to the local engine until the next start"


def _post_local_voice(c: RemedyCtx):
    return c.core.voice_mode == "push_to_talk", f"voice mode {c.core.voice_mode}"


def _identity(c: RemedyCtx):
    from ..identity import Identity
    return Identity(Path(c.home), c.cfg if c.cfg is not None else getattr(c.core, "cfg", None))


def _pre_stock_wake(c: RemedyCtx):
    try:
        cur = _identity(c).wake().get("model", "hey_jarvis")
    except Exception as e:
        return False, f"identity not readable ({type(e).__name__})"
    return (cur != "hey_jarvis", "already on the stock Hey Jarvis model" if cur == "hey_jarvis" else
            f"current model {Path(str(cur)).name}")


async def _act_stock_wake(c: RemedyCtx):
    ident = _identity(c)
    ident.set_wake("hey_jarvis", "Hey Jarvis", None, by="health")
    if c.core is not None:
        ident.apply_to_config(c.core.cfg)
        await _act_wake(c)
        return "stock Hey Jarvis model active now (the custom model file is kept)"
    return "stock Hey Jarvis model from the next start (the custom model file is kept)"


def _post_stock_wake(c: RemedyCtx):
    return _identity(c).wake().get("model") == "hey_jarvis", "wake model hey_jarvis"


def _pre_feature(c: RemedyCtx):
    fid = (c.args or {}).get("feature", "")
    if c.features is None or not fid:
        return False, "which feature? (args.feature)"
    from ..features import CATALOG
    if fid not in CATALOG:
        return False, f"unknown feature {fid}"
    ok, why = c.features.available(fid)
    return (not ok, f"{fid} already available" if ok else f"{fid}: {why}")


def _act_feature(c: RemedyCtx):
    from ..feature_setup import prepare
    fid = c.args["feature"]
    ok, msg = prepare(fid, c.features, Path(c.home))
    c.features.invalidate(fid)
    return f"feature_setup {fid}: {'ok' if ok else 'failed'} {msg}"[:200]


def _post_feature(c: RemedyCtx):
    fid = c.args["feature"]
    ok, why = c.features.available(fid)
    return ok, f"{fid} available" if ok else f"{fid}: {why}"


def _pre_rollback(c: RemedyCtx):
    from .. import updates
    fn = getattr(updates, "rollback", None)
    if fn is None:
        return False, "rollback is not available in this build (package L adds it)"
    can = getattr(updates, "can_rollback", None)
    if can is not None:
        ok, why = can(Path(c.home))
        return ok, why
    return True, "ready"


def _act_rollback(c: RemedyCtx):
    from .. import updates
    return str(updates.rollback(Path(c.home)))[:200]


def _post_rollback(c: RemedyCtx):
    from .. import updates
    chk = getattr(updates, "rollback_status", None)
    return (chk(Path(c.home)) if chk else (True, "rollback started (verify after the restart)"))


def _ok(c):
    return True, "ready"


REMEDIES: dict[str, Remedy] = {r.id: r for r in (
    Remedy("restart_face_link", "Restart the face link", "הפעלה מחדש של החיבור לפנים",
           "Close and reopen the localhost link to the face window (a few seconds without the face).",
           "סגירה ופתיחה מחדש של החיבור לחלון הפנים (כמה שניות בלי פנים).", True, _pre_core("link"), _act_face_link,
           _post_face_link, grantable=True),
    Remedy("restart_wake_loop", "Restart the wake-word loop", "הפעלה מחדש של מילת ההפעלה",
           "Stop the wake-word listener and start it again with the current model.",
           "עצירה והפעלה מחדש של האזנה למילת ההפעלה.", True, _pre_core("dev"), _act_wake, _post_wake),
    Remedy("restart_audio_device", "Re-open the audio devices", "פתיחה מחדש של התקני השמע",
           "Probe the microphone and speaker again (after a device was plugged in or changed).",
           "בדיקה מחדש של המיקרופון והרמקול.", True, _pre_core("dev"), _act_audio, _post_audio),
    Remedy("restart_dashboard", "Restart the dashboard server", "הפעלה מחדש של לוח הבקרה",
           "Stop the dashboard server inside JARVES and start it on a new port (open tabs must reload).",
           "עצירה והפעלה מחדש של שרת לוח הבקרה.", True, _pre_core("home"), _act_dashboard, _post_dashboard),
    Remedy("restart_bridges", "Restart the bridges", "הפעלה מחדש של הגשרים",
           "Restart the Agent Orchestrator and WhatsApp bridges (read-only status is checked after).",
           "הפעלה מחדש של הגשרים (סוכנים, וואטסאפ).", True, _pre_bridges, _act_bridges, _post_bridges),
    Remedy("clear_speech_cache", "Clear the speech cache", "ניקוי מטמון הדיבור",
           "Delete the cached greeting audio; it is recorded again on the next greeting.",
           "מחיקת קובצי הקול השמורים של הברכה; הם נשמרים מחדש בברכה הבאה.", False, _ok, _act_speech,
           lambda c: (not _speech_files(c), f"{len(_speech_files(c))} left")),
    Remedy("clear_pear_probe_cache", "Clear the Pear probe cache", "ניקוי בדיקת Pear השמורה",
           "Forget the cached 'is Pear running?' answer so the next check asks Pear again.",
           "שכחת התשובה השמורה האם Pear פועל, כדי שהבדיקה הבאה תשאל שוב.", False, _pre_pear, _act_pear, _post_pear),
    Remedy("clear_guide_cache", "Clear the guide cache", "ניקוי מטמון המדריכים",
           "Reload the in-app guides from their files.", "טעינה מחדש של המדריכים מהקבצים.", False, _ok,
           _act_guides, _post_guides),
    Remedy("reconnect_google", "Reconnect Google", "חיבור מחדש של גוגל",
           "Open the existing Google reconnect flow (you sign in; nothing else changes).",
           "פתיחת תהליך החיבור מחדש של גוגל (אתם מתחברים; שום דבר אחר לא משתנה).", False, _ok, _act_google,
           _post_google),
    Remedy("fallback_local_voice", "Switch to the local voice engine", "מעבר למנוע הקול המקומי",
           "Use the local speech-to-text / text-to-speech until the next start (GPT-Live is down).",
           "שימוש בזיהוי ובדיבור המקומיים עד ההפעלה הבאה (GPT-Live לא זמין).", True, _pre_local_voice,
           _act_local_voice, _post_local_voice),
    Remedy("fallback_stock_wake", "Use the stock wake model", "מעבר למודל ההפעלה הרגיל",
           "Go back to the standard Hey Jarvis model (the custom model file is kept).",
           "חזרה למודל Hey Jarvis הרגיל (קובץ המודל המותאם נשמר).", False, _pre_stock_wake, _act_stock_wake,
           _post_stock_wake),
    Remedy("rerun_feature_setup", "Re-run a feature's setup", "הרצה מחדש של הגדרת יכולת",
           "Install the missing prerequisite of one switched-on feature again (feature_setup).",
           "התקנה מחדש של דרישה חסרה של יכולת דולקת.", False, _pre_feature, _act_feature, _post_feature),
    Remedy("rollback_update", "Roll back the last update", "החזרת העדכון האחרון",
           "Go back to the previous installed version (JARVES restarts).", "חזרה לגרסה הקודמת (ג'ארביס מופעל מחדש).",
           False, _pre_rollback, _act_rollback, _post_rollback),
)}


def catalog() -> list[dict]:
    return [{"id": r.id, "title": r.title, "title_he": r.title_he, "dry_run": r.dry_run, "dry_run_he": r.dry_run_he,
             "risk": r.risk, "needs_core": r.needs_core, "grantable": r.grantable} for r in REMEDIES.values()]


def _record(c: RemedyCtx, rid: str, status: str, *, finding: str | None, approved: bool, automatic: bool,
            pre: str, result: str = "", post: str = "", by: str = "") -> int:
    return db.write(c.store, "INSERT INTO diag_remediations (ts, remedy, finding, status, approved, automatic, "
                             "precheck, result, postcheck, by, args) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (c.clock(), rid, finding, status, int(approved), int(automatic), pre[:300], result[:300],
                     post[:300], by, db.dumps(c.args or {})))


async def run(rid: str, c: RemedyCtx, *, by: str, finding: str | None = None, automatic: bool = False,
              db_settings=None) -> dict:
    """Pre-check -> APPROVAL -> action -> post-check. Returns the recorded row as a dict."""
    r = REMEDIES.get(rid)
    if r is None:
        raise KeyError(rid)
    try:
        ok, pre = r.pre(c)
    except Exception as e:
        ok, pre = False, f"pre-check failed: {type(e).__name__}"
    if not ok:
        rowid = _record(c, rid, "precheck_failed", finding=finding, approved=False, automatic=False, pre=pre, by=by)
        logs.info("health", "REMEDY_PRECHECK_FAILED", "remedy not possible now", remedy=rid)
        return history(c.store, rowid)
    allowed_auto = bool(automatic and r.grantable and db_settings is not None
                        and db_settings.setting("health.auto_face_link_restart"))
    if allowed_auto:
        approved = True
    else:
        if c.ask is None:
            rowid = _record(c, rid, "denied", finding=finding, approved=False, automatic=False, pre=pre,
                            result="no approval channel", by=by)
            return history(c.store, rowid)
        try:
            approved = bool(await c.ask(f"{r.title}?\n{r.dry_run}", title=r.title, why=f"health: {finding or rid}",
                                        risk=r.risk, kind="other", details={"remedy": rid, "finding": finding or ""}))
        except Exception:
            approved = False
    if not approved:
        rowid = _record(c, rid, "denied", finding=finding, approved=False, automatic=False, pre=pre, by=by)
        logs.info("health", "REMEDY_DENIED", "remedy not approved: nothing done", remedy=rid)
        return history(c.store, rowid)
    try:
        result = str(await _maybe(r.act(c)) or "")
    except Exception as e:
        rowid = _record(c, rid, "failed", finding=finding, approved=True, automatic=allowed_auto, pre=pre,
                        result=f"{type(e).__name__}: {logs.redact(str(e))[:160]}", by=by)
        logs.warn("health", "REMEDY_FAILED", "remedy failed", remedy=rid, error_type=type(e).__name__)
        return history(c.store, rowid)
    try:
        pok, post = r.post(c)
    except Exception as e:
        pok, post = False, f"post-check failed: {type(e).__name__}"
    rowid = _record(c, rid, "done" if pok else "post_check_failed", finding=finding, approved=True,
                    automatic=allowed_auto, pre=pre, result=result, post=post, by=by)
    logs.info("health", "REMEDY_DONE", "remedy done", remedy=rid,
              ok=pok, automatic=allowed_auto)
    return history(c.store, rowid)


def history(store, rowid: int | None = None, limit: int = 30) -> dict | list[dict]:
    if rowid is not None:
        r = db.rows(store, "SELECT * FROM diag_remediations WHERE id=?", (rowid,))
        if r:
            r[0]["args"] = db.j(r[0]["args"], {})
            r[0]["approved"], r[0]["automatic"] = bool(r[0]["approved"]), bool(r[0]["automatic"])
        return r[0] if r else {}
    out = db.rows(store, "SELECT * FROM diag_remediations ORDER BY id DESC LIMIT ?", (limit,))
    for r in out:
        r["args"] = db.j(r["args"], {})
    return out
