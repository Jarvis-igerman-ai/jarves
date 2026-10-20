"""Feature guides (SW-V2 WP-L, Issue #17): ONE source of truth for "what is this, is it on, how do I set it up,
what do I say, how do I test it" - read by the dashboard Guides pages AND by JARVES himself.

Data: one TOML file per guide in guides/data/<id>.toml (Hebrew + English text, steps, example phrases, problems).
Everything that describes the CURRENT state (on / off / unavailable and why, Windows / Raspberry Pi status) is
generated from jarves.features (status() + targets) and a few local probes, never written in the guide text.
Every feature registered in jarves.features must be covered by exactly one guide (tests/test_guides.py fails
otherwise).

What JARVES gets from it:
- `briefing(f)`         compact "what I can do / what needs setup and why" for his instructions (size-capped,
                        recomputed on every session / request, so a switch flipped on the dashboard shows at once)
- `index_markdown()`    the guide index for TOOLS.md
- `make_skill(...)`     the help Skill: explain_feature, whats_not_set_up, setup_next_step, run_guide_step,
                        open_guide, setup_snooze, stop_suggestions (one step at a time; risky steps need the
                        on-screen approval of registry.confirm)
- `Suggester`           at most ONE short setup suggestion per conversation, at most once per guide every
                        SUGGEST_EVERY_DAYS days, never in quiet hours, persisted snooze / dismiss, switch
                        `proactive_help` ("תפסיק להציע")
- `setup_hint(...)`     machine-readable hint attached to FEATURE_OFF / unconfigured tool errors

State file: <state_dir>/guides_state.json (progress per guide, suggestions made, snoozed, dismissed). No secrets.
Dashboard: GET /api/guides, GET /api/guides/<id>, POST /api/guides/<id>/test, POST /api/guides/<id>/step/<n>,
POST /api/guides-help (snooze / dismiss / reset). Tests ("Test now") are local checks only, with two exceptions
that ask the network on a button press: the OpenAI admin key check (read-only Costs API, free) and nothing else.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import features as feat
from .. import hebrew, logs

DATA = Path(__file__).with_name("data")
STATE_FILE = "guides_state.json"
SUGGEST_EVERY_DAYS = 7.0
BRIEFING_BACKEND_CHARS = 2600
BRIEFING_FRONT_CHARS = 900
STATES = ("on", "setup", "off", "partly", "planned", "missing", "info")
# A guide may name features another package registers later ("missing" until then); the test demands a guide for
# every REGISTERED feature. This package's own switch:
feat.register("proactive_help", "הצעות עזרה בהגדרה", "Setup suggestions", True,
              about="now and then suggests one setup step in a short sentence (\"תפסיק להציע\" turns it off)",
              targets={"windows": {"status": "implemented_unverified",
                                   "evidence": "core/tests/test_guides.py (scripted conversations, offline)"},
                       "linux_arm64_pi": {"status": "implemented_unverified",
                                          "roadmap_issue": "https://github.com/igalgerman/JARVIS/issues/25",
                                          "blocker": "pure Python, no platform code; not run on a Pi yet"}})


# ---------------------------------------------------------------------- data
@dataclass
class Step:
    he: str
    en: str
    action: str = ""          # feature_on:<id> | open:<#/page> | command:<text> | test | "" (the user does it)
    check: str = ""           # enabled:<id> | available:<id> | active:<id> | probe:<name> | test | ""


@dataclass
class Guide:
    id: str
    title_he: str
    title_en: str
    what_he: str
    what_en: str
    order: int = 100
    category: str = "other"
    features: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    probe: str = ""           # state for a guide without features (EXTRA_PROBES)
    optional: bool = False    # a failing probe means "off by choice", not "needs setup" (privacy, admin key)
    status: str = ""          # "planned" for things not built yet
    checklist: int = 0        # position in "What should I do first?" (0 = not in it)
    suggest: bool = False     # may JARVES propose it by himself?
    steps: list[Step] = field(default_factory=list)
    say: list[tuple[str, str]] = field(default_factory=list)
    expect_he: str = ""
    expect_en: str = ""
    test: str = ""            # TESTS key ("" = none; "feature" = re-check availability)
    test_he: str = ""
    test_en: str = ""
    cost_he: str = ""
    cost_en: str = ""
    privacy_he: str = ""
    privacy_en: str = ""
    problems: list[dict] = field(default_factory=list)   # {he, en, fix_he, fix_en}
    error_codes: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)       # related guide ids
    pages: list[str] = field(default_factory=list)       # dashboard pages that show a "Guide" link to this one
    platform: dict = field(default_factory=dict)        # fallback {windows, pi, pi_note_en} when no feature targets


_cache: dict[str, Any] = {"key": None, "guides": {}}
_lock = threading.RLock()


def _parse(path: Path) -> Guide:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    raw["id"] = raw.get("id") or path.stem
    raw["steps"] = [Step(**s) for s in raw.get("steps", [])]
    raw["say"] = [tuple(x) for x in raw.get("say", [])]
    return Guide(**raw)


def load(data_dir: Path | None = None) -> dict[str, Guide]:
    """All guides by id (sorted by order), re-read when a file changed (hot reload while editing)."""
    d = data_dir or DATA
    files = sorted(d.glob("*.toml"))
    key = (str(d), tuple((p.name, p.stat().st_mtime_ns) for p in files))
    with _lock:
        if _cache["key"] == key:
            return _cache["guides"]
        out = {}
        for p in files:
            try:
                g = _parse(p)
            except (OSError, ValueError, TypeError) as e:
                logs.warn("guides", "GUIDE_BAD", f"guide {p.name} unreadable", error=type(e).__name__)
                continue
            out[g.id] = g
        out = dict(sorted(out.items(), key=lambda kv: (kv[1].order, kv[0])))
        _cache.update(key=key, guides=out)
        return out


def for_feature(fid: str) -> Guide | None:
    return next((g for g in load().values() if fid in g.features), None)


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-\"'״׳.?!,]+", " ", s.lower()).strip()


def find(name: str) -> Guide | None:
    """A guide by id, feature id, title or alias (Hebrew or English, partial words allowed)."""
    gs = load()
    n = _norm(name or "")
    if not n:
        return None
    if n.replace(" ", "_") in gs:
        return gs[n.replace(" ", "_")]
    for g in gs.values():
        if n.replace(" ", "_") in g.features:
            return g
    best, score = None, 0
    for g in gs.values():
        names = [g.id, g.title_he, g.title_en, *g.aliases, *g.features]
        for x in names:
            x = _norm(x)
            if not x:
                continue
            s = 3 if x == n else 2 if (n in x or x in n) and min(len(n), len(x)) >= 3 else 0
            if s == 0 and len(n) >= 3:
                common = set(n.split()) & set(x.split())
                s = 1 if any(len(w) >= 3 for w in common) else 0
            if s > score:
                best, score = g, s
    return best


# ---------------------------------------------------------------------- live state
def _probe_ai(cfg, home) -> tuple[bool, str]:
    return feat._ai_online(feat.ProbeCtx(cfg, home, lambda: {}, lambda: None))


def _probe_privacy(cfg, home) -> tuple[bool, str]:
    on = bool(getattr(getattr(cfg, "log", None), "privacy", False))
    try:                                  # WP-K's privacy switch (flag file, applies at once in every process)
        on = bool(json.loads((home / "logs" / logs.PRIVACY_FILE).read_text(encoding="utf-8"))["privacy"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return on, "" if on else "privacy mode is off: what you say is kept in the local history (never uploaded)"


def _probe_admin_key(cfg, home) -> tuple[bool, str]:
    from ..usage import ADMIN_ENV
    return (True, "") if os.environ.get(ADMIN_ENV, "").strip() else (False, "no OpenAI admin key saved")


def _probe_budget(cfg, home) -> tuple[bool, str]:
    if home is None:
        return False, "no data folder"
    from ..usage import get_settings
    b = get_settings(home).get("api_monthly_budget_usd")
    return (True, f"${b:g} a month") if b else (False, "no monthly API budget set")


def _probe_backup(cfg, home) -> tuple[bool, str]:
    if home is None:
        return False, "no data folder"
    from .. import backup
    items = backup.list_backups(home)
    return (True, items[0]["name"]) if items else (False, "no backup made yet")


EXTRA_PROBES: dict[str, Callable] = {"ai": _probe_ai, "privacy": _probe_privacy, "admin_key": _probe_admin_key,
                                     "budget": _probe_budget, "backup": _probe_backup}


def _check(spec: str, f: feat.Features) -> tuple[bool, str]:
    """A step check: (passed, why not)."""
    kind, _, arg = spec.partition(":")
    if kind in ("enabled", "available", "active") and arg not in feat.CATALOG:
        return False, f"{arg} is not in this version"
    if kind == "enabled":
        return (True, "") if f.enabled(arg) else (False, f"{feat.CATALOG[arg].title_en} is switched off")
    if kind == "available":
        f.invalidate(arg)
        return f.available(arg)
    if kind == "active":
        f.invalidate(arg)
        return (True, "") if f.active(arg) else (False, f.reason(arg))
    if kind == "probe" and arg in EXTRA_PROBES:
        return EXTRA_PROBES[arg](f.cfg, f.home)
    return True, ""


def state(g: Guide, f: feat.Features) -> dict:
    """Live state of one guide: on / setup (needs setup) / off / partly / planned / info, with reasons."""
    rows = [f.status(fid) for fid in g.features if fid in feat.CATALOG]
    missing = [fid for fid in g.features if fid not in feat.CATALOG]
    reason = reason_he = ""
    if g.status == "planned" and not rows:
        st = "planned"
    elif g.features and not rows:
        st, reason = "missing", "not in this version yet"
        reason_he = hebrew.he(reason) or ""
    elif rows:
        main = rows[0]
        broken = [r for r in rows[1:] if r["enabled"] and not r["available"]]   # extras switched off = a choice
        if main["active"] and not broken:
            st = "on"
        elif main["enabled"] and not main["available"]:
            st, reason = "setup", main["reason"]
            reason_he = main.get("reason_he", "")
        elif not main["enabled"]:
            st, reason = "off", "turned off" if main["available"] else f"turned off; also {main['reason']}"
            reason_he = "כבוי" if main["available"] else (f"כבוי; וגם {main['reason_he']}" if main.get("reason_he") else "")
        else:
            st = "partly"
            reason = f"{broken[0]['title_en']}: {broken[0]['reason']}"
            reason_he = f"{broken[0]['title_he']}: {broken[0]['reason_he']}" if broken[0].get("reason_he") else ""
    elif g.probe in EXTRA_PROBES:
        try:
            ok, why = EXTRA_PROBES[g.probe](f.cfg, f.home)
        except Exception as e:
            ok, why = False, f"check failed ({type(e).__name__})"
        st, reason = ("on", why) if ok else ("off" if g.optional else "setup", why)
        reason_he = hebrew.he(why) or ""
    else:
        st = "info"
    return {"state": st, "reason": reason, "reason_he": reason_he, "features": rows, "missing_features": missing,
            "setup_needed": st == "setup"}


def platforms(g: Guide, rows: list[dict]) -> dict:
    """Windows / Raspberry Pi status: the features' own targets (truthful, per feature), else the guide's note."""
    for r in rows:
        t = r.get("targets") or {}
        if t:
            return {"windows": t.get("windows"), "pi": t.get("linux_arm64_pi"), "source": r["id"]}
    p = g.platform or {}
    return {"windows": hebrew.attach({"status": p.get("windows", "implemented_unverified"),
                                      "evidence": p.get("windows_note_en", "")}, "evidence"),
            "pi": hebrew.attach({"status": p.get("pi", "implemented_unverified"), "blocker": p.get("pi_note_en", "")},
                                "blocker"),
            "source": "guide"}


# ---------------------------------------------------------------------- safe tests ("Test now")
def _t_feature(g: Guide, f: feat.Features) -> tuple[bool, str, str]:
    if not g.features:
        return True, "אין מה לבדוק כאן.", "Nothing to check here."
    bad = []
    for fid in g.features:
        if fid not in feat.CATALOG:
            continue
        f.invalidate(fid)
        if not f.active(fid) and f.enabled(fid):
            bad.append(f"{feat.CATALOG[fid].title_en}: {f.reason(fid)}")
    if bad:
        return False, "עוד לא מוכן: " + "; ".join(bad), "Not ready yet: " + "; ".join(bad)
    if not any(f.enabled(x) for x in g.features if x in feat.CATALOG):
        return False, "כבוי. הפעילו את המתג קודם.", "It is switched off. Turn the switch on first."
    return True, "הכול מוכן ופועל.", "Ready and working."


def _t_music(g, f):
    from ..features import _tcp_open
    url = f.cfg.music.pear_url
    if not _tcp_open(url):
        return (False, "Pear Desktop לא עונה. פתחו את Pear והפעילו את התוסף API Server.",
                f"Pear Desktop does not answer at {url}. Open Pear and enable its API Server plugin.")
    f.invalidate("music")
    if not f.enabled("music"):
        return False, "Pear עונה, אבל המוזיקה כבויה. הפעילו את המתג.", "Pear answers, but Music is switched off."
    return (True, "Pear Desktop עונה. אמרו \"תנגן משהו רגוע\" כדי לשמוע.",
            "Pear Desktop answers (nothing was played). Say \"play something calm\" to hear it.")


def _t_camera(g, f):
    f.invalidate("camera")
    ok, why = f.available("camera")
    if not ok:
        return False, f"לא מצאתי מצלמה: {why}", f"No camera found: {why}"
    on = f.enabled("camera")
    return (True, "מצלמה נמצאה. " + ("היא פועלת רק כשמבקשים, ואז נורית המצלמה נדלקת." if on else
                                     "המצלמה כבויה עכשיו, אז הנורית שלה צריכה להיות כבויה."),
            "Camera found. " + ("It is used only when you ask; its light turns on then." if on else
                                "The camera is switched off now, so its light must be off."))


def _t_backup(g, f):
    from .. import backup
    if f.home is None:
        return False, "אין תיקיית נתונים.", "No data folder."
    items = backup.list_backups(f.home)
    if not items:
        return False, "עוד אין גיבוי. לחצו \"גבה עכשיו\" בעמוד הגיבוי.", "No backup yet. Press Back up now on the Backup page."
    res = backup.verify(backup.backup_dir(f.home) / items[0]["name"])
    if res.get("ok"):
        return True, f"הגיבוי האחרון ({items[0]['name']}) תקין.", f"The newest backup ({items[0]['name']}) is intact."
    return False, "הגיבוי האחרון פגום: " + str(res.get("error") or res.get("errors")), \
        "The newest backup is damaged: " + str(res.get("error") or res.get("errors"))


def _t_ai(g, f):
    ok, why = _probe_ai(f.cfg, f.home)
    return (True, "החיבור ל-OpenAI מוגדר (לא נשלחה בקשה).", "The OpenAI connection is set up (no request was sent).") \
        if ok else (False, f"עוד לא מחובר: {why}", f"Not connected yet: {why}")


def _t_admin_key(g, f):
    """Real check on a button press: the read-only Costs API (free, no model call)."""
    from ..usage import Monitor
    if not _probe_admin_key(f.cfg, f.home)[0]:
        return False, "לא נשמר מפתח אדמין. הריצו jarvis key --admin.", "No admin key saved. Run jarvis key --admin."
    res = Monitor(f.home).refresh_costs(force=True) or {}
    if "usd" in res and not res.get("stale_error"):
        return True, f"עובד: החיוב האמיתי החודש ${res['usd']:.2f}.", f"Works: the real bill this month is ${res['usd']:.2f}."
    err = res.get("stale_error") or res.get("error") or "no answer"
    return False, f"OpenAI לא אישר את המפתח ({err}).", f"OpenAI did not accept the key ({err})."


TESTS: dict[str, Callable] = {"feature": _t_feature, "music": _t_music, "camera": _t_camera, "backup": _t_backup,
                              "ai": _t_ai, "admin_key": _t_admin_key}


def run_test(g: Guide, f: feat.Features) -> dict:
    fn = TESTS.get(g.test or "feature", _t_feature)
    try:
        ok, he, en = fn(g, f)
    except Exception as e:
        ok, he, en = False, f"הבדיקה נכשלה ({type(e).__name__}).", f"The check failed ({type(e).__name__})."
    logs.info("guides", "GUIDE_TEST", f"guide test {g.id}: {'ok' if ok else 'not ok'}", guide=g.id, ok=ok)
    return {"ok": bool(ok), "he": he, "en": en, "ts": time.time()}


# ---------------------------------------------------------------------- views
def summary(g: Guide, f: feat.Features) -> dict:
    s = state(g, f)
    return {"id": g.id, "title_he": g.title_he, "title_en": g.title_en, "what_he": g.what_he, "what_en": g.what_en,
            "category": g.category, "order": g.order, "checklist": g.checklist, "features": g.features,
            "pages": g.pages,
            "state": s["state"], "reason": s["reason"], "reason_he": s["reason_he"],
            "setup_needed": s["setup_needed"], "has_test": bool(g.test or g.features)}


def full(g: Guide, f: feat.Features, progress: int = 0) -> dict:
    s = state(g, f)
    steps = []
    for i, st in enumerate(g.steps):
        done = None
        if st.check and st.check != "test":
            try:
                done = _check(st.check, f)[0]
            except Exception:
                done = None
        steps.append({"n": i + 1, "he": st.he, "en": st.en, "action": st.action, "check": st.check, "done": done})
    return {**summary(g, f), "steps": steps, "say": [{"he": he, "en": en} for he, en in g.say],
            "expect_he": g.expect_he, "expect_en": g.expect_en, "test": g.test or ("feature" if g.features else ""),
            "test_he": g.test_he, "test_en": g.test_en, "cost_he": g.cost_he, "cost_en": g.cost_en,
            "privacy_he": g.privacy_he, "privacy_en": g.privacy_en, "problems": g.problems,
            "feature_rows": [{k: r[k] for k in ("id", "title_he", "title_en", "enabled", "available", "active",
                                                "reason", "reason_he", "risk", "applies") if k in r} for r in s["features"]],
            "missing_features": s["missing_features"], "platforms": platforms(g, s["features"]),
            "links": [{"id": x, "title_en": load()[x].title_en, "title_he": load()[x].title_he}
                      for x in g.links if x in load()], "progress": progress}


def checklist(f: feat.Features) -> list[dict]:
    """'What should I do first?': the checklist guides in order, done = their state is on."""
    out = []
    for g in sorted((g for g in load().values() if g.checklist), key=lambda g: g.checklist):
        s = state(g, f)
        out.append({"id": g.id, "title_he": g.title_he, "title_en": g.title_en, "done": s["state"] == "on",
                    "state": s["state"], "reason": s["reason"], "reason_he": s["reason_he"],
                    "first_step_he": g.steps[0].he if g.steps else "", "first_step_en": g.steps[0].en if g.steps else ""})
    return out


# ---------------------------------------------------------------------- for JARVES
def _grouped_setup(f: feat.Features) -> list[tuple[str, list[Guide]]]:
    """Guides that need setup, grouped by the same reason (one missing key blocks several features)."""
    groups: dict[str, list[Guide]] = {}
    for g in load().values():
        s = state(g, f)
        if s["state"] in ("setup", "partly"):
            groups.setdefault(s["reason"] or "not set up", []).append(g)
    return list(groups.items())


def briefing(f: feat.Features, limit: int = BRIEFING_BACKEND_CHARS) -> str:
    """'What I can do + how to set it up', generated from the guides and the live switches. Size-capped."""
    on, off, planned = [], [], []
    for g in load().values():
        s = state(g, f)
        if s["state"] == "on":
            on.append(g.title_en)
        elif s["state"] == "off":
            off.append(f"{g.title_en} ({s['reason']})")
        elif s["state"] in ("planned", "missing"):
            planned.append(g.title_en)
    lines = ["## What I can do and how to set it up (guides)",
             "Ready now: " + (", ".join(on) or "nothing yet") + ".",
             "Needs setup (offer to help, one step at a time; guide id in brackets):"]
    for why, gs in _grouped_setup(f):
        first = gs[0].steps[0].en if gs[0].steps else ""
        lines.append(f"- {why}: " + ", ".join(f"{g.title_he} / {g.title_en} [{g.id}]" for g in gs)
                     + f". First step: {first}")
    if len(lines) == 3:
        lines.append("- nothing")
    lines.append("Off by the user's choice (can be turned on in the dashboard Features page or by asking): "
                 + (", ".join(off) or "none") + ".")
    if planned:
        lines.append("Not built yet (say so honestly): " + ", ".join(planned) + ".")
    lines.append("For \"what can you do?\" answer from this list in a few words. For \"how do I set up / turn on X?\" "
                 "call explain_feature, then setup_next_step and walk the user through ONE step at a time; wait for "
                 "\"done\"/\"next\" and call setup_next_step again with done=true (it checks the result). Use "
                 "run_guide_step for safe steps and open_guide when a picture helps. Never claim something works "
                 "before its check passed.")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit - 20].rsplit("\n", 1)[0] + "\n(more: explain_feature)"
    return text + "\n"


def front_briefing(f: feat.Features, limit: int = BRIEFING_FRONT_CHARS) -> str:
    """Short version for GPT-Live itself: what needs setup, and that help questions go to the backend."""
    need = [f"{', '.join(g.title_he for g in gs)} ({why})" for why, gs in _grouped_setup(f)]
    text = ("When the user asks what you can do, or how to set up / turn on / connect something, delegate it: the "
            "backend has step-by-step guides (explain_feature, setup_next_step).")
    if need:
        text += " Needs setup now: " + "; ".join(need)
    return text[:limit] + ("." if not text.endswith(".") else "")


def index_markdown() -> str:
    """The guide index for TOOLS.md."""
    items = ", ".join(f"{g.id} ({g.title_he})" for g in load().values())
    return ("## Guides (explain_feature NAME; dashboard #/guides)\n" + items + "\n")


def setup_hint(fid: str | None, f: feat.Features, *, code: str = "FEATURE_OFF") -> dict | None:
    """Machine-readable hint attached to an unconfigured / switched-off tool error."""
    g = for_feature(fid) if fid else None
    if g is None:
        return None
    s = state(g, f)
    nxt = next((st for st in g.steps if not st.check or not _check(st.check, f)[0]), g.steps[0] if g.steps else None)
    return {"guide": g.id, "feature": fid, "state": s["state"], "reason": s["reason"] or f.reason(fid),
            "next_step_he": nxt.he if nxt else "", "next_step_en": nxt.en if nxt else "",
            "dashboard": f"#/guide/{g.id}", "code": code,
            "do": "tell the user in one short sentence why it does not work and offer to set it up "
                  "(setup_next_step)"}


def guide_for_code(code: str) -> Guide | None:
    return next((g for g in load().values() if code in g.error_codes), None)


# ---------------------------------------------------------------------- per-user state + suggestions
class GuideState:
    """<home>/guides_state.json: progress (steps confirmed), shown (step read out last), suggestions made, snoozed,
    dismissed (thread-safe, atomic writes)."""
    KEYS = ("progress", "shown", "suggested", "snoozed", "dismissed")

    def __init__(self, home: Path | None) -> None:
        self.path = home / STATE_FILE if home is not None else None
        self._lock = threading.RLock()
        self._mem: dict = {k: {} for k in self.KEYS}

    def read(self) -> dict:
        with self._lock:
            if self.path is None:
                return {k: dict(v) for k, v in self._mem.items()}
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return {k: dict(raw.get(k) or {}) for k in self.KEYS}
            except (OSError, ValueError):
                pass
            return {k: {} for k in self.KEYS}

    def update(self, fn: Callable[[dict], None]) -> dict:
        with self._lock:
            d = self.read()
            fn(d)
            if self.path is None:
                self._mem = d
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, self.path)
            return d

    def progress(self, gid: str) -> int:
        return int(self.read()["progress"].get(gid, 0))

    def set_progress(self, gid: str, n: int) -> None:
        self.update(lambda d: d["progress"].__setitem__(gid, int(n)))

    def snooze(self, gid: str, days: float) -> None:
        until = time.time() + days * 86400
        self.update(lambda d: d["snoozed"].__setitem__(gid, until))

    def dismiss(self, gid: str) -> None:
        self.update(lambda d: d["dismissed"].__setitem__(gid, time.time()))

    def reset(self) -> None:
        self.update(lambda d: (d["snoozed"].clear(), d["dismissed"].clear(), d["suggested"].clear()))


@dataclass
class Suggestion:
    guide: Guide
    why: str
    reason: str

    def text(self) -> str:
        return (f"{self.guide.title_he} / {self.guide.title_en} is not set up ({self.reason}). "
                f"First step: {self.guide.steps[0].en if self.guide.steps else ''}")


class Suggester:
    """Picks at most one setup suggestion: features the diagnosis saw failing first, then switched-on features
    that are unavailable, then the first-run checklist. Each guide at most once per `every_days` (persisted),
    never when snoozed / dismissed, in quiet hours or with `proactive_help` off."""

    def __init__(self, features: feat.Features, state: GuideState, *, quiet: Callable[[], bool] | None = None,
                 failing: Callable[[], set[str]] | None = None, every_days: float = SUGGEST_EVERY_DAYS,
                 clock: Callable[[], float] = time.time) -> None:
        self.f, self.st = features, state
        self.quiet = quiet or (lambda: False)
        self.failing = failing or (lambda: set())
        self.every_s = every_days * 86400
        self.clock = clock

    def _allowed(self, gid: str, d: dict, now: float) -> bool:
        if gid in d["dismissed"]:
            return False
        if float(d["snoozed"].get(gid, 0)) > now:
            return False
        return now - float(d["suggested"].get(gid, 0)) >= self.every_s

    def pick(self) -> Suggestion | None:
        if "proactive_help" in feat.CATALOG and not self.f.enabled("proactive_help"):
            return None
        try:
            if self.quiet():
                return None
        except Exception:
            return None
        d, now = self.st.read(), self.clock()
        gs = load()
        try:
            fails = set(self.failing() or ())
        except Exception:
            fails = set()
        cands: list[tuple[int, Guide, str]] = []
        for g in gs.values():
            if not g.suggest:
                continue
            s = state(g, self.f)
            if g.id in fails and s["state"] != "off":
                cands.append((0, g, "failing"))
            elif s["state"] == "setup":
                cands.append((1 if g.checklist else 2, g, "setup"))      # first-run checklist order first
        cands.sort(key=lambda c: (c[0], gs[c[1].id].checklist or 99, c[1].order))
        for _, g, why in cands:
            if self._allowed(g.id, d, now):
                return Suggestion(g, why, state(g, self.f)["reason"] or "it is not working")
        return None

    def mark(self, s: Suggestion) -> None:
        now = self.clock()
        self.st.update(lambda d: d["suggested"].__setitem__(s.guide.id, now))
        logs.info("guides", "GUIDE_SUGGESTED", f"suggested setup of {s.guide.id}", guide=s.guide.id, why=s.why)

    def take(self) -> Suggestion | None:
        s = self.pick()
        if s is not None:
            self.mark(s)
        return s


SUGGEST_NOTE = ("Setup suggestion for this conversation (optional): mention it ONCE in one short, friendly sentence, "
                "only after you answered what the user wanted or at a natural pause, never while the user is speaking "
                "and never as the first thing if the user asked something. Offer help, do not insist. If the user "
                "says no / later, drop it and delegate setup_snooze; if they say \"stop suggesting\" (\"תפסיק "
                "להציע\"), delegate stop_suggestions. Suggestion: ")


def attach_live(live, features: feat.Features, suggester: Suggester | None = None) -> None:
    """Add the short front briefing (+ at most one suggestion per conversation) to a LiveVoice's instructions."""
    orig = getattr(live, "session_config", None)
    if orig is None:
        return
    picked: list = []

    def session_config() -> dict:
        cfg = orig()
        extra = front_briefing(features)
        if suggester is not None:
            if not picked:                      # once per conversation (a reconnect reuses the same choice)
                picked.append(suggester.take())
            if picked[0] is not None:
                extra += "\n" + SUGGEST_NOTE + picked[0].text()
        cfg["instructions"] = f"{cfg.get('instructions', '')}\n\n{extra}"
        return cfg
    live.session_config = session_config


def augment_instructions(agent, features: feat.Features) -> None:
    """Backend brain: the full briefing, recomputed for every request (hot: follows switches at once)."""
    base = agent.instructions

    def instructions() -> str:
        try:
            extra = briefing(features)
        except Exception:
            logs.error("guides", "GUIDE_BRIEFING_FAILED", "guide briefing failed", exc=True)
            extra = ""
        return f"{base()}\n\n{extra}" if extra else base()
    agent.instructions = instructions


# ---------------------------------------------------------------------- the help Skill
def _step_text(g: Guide, i: int, total: int) -> str:
    st = g.steps[i]
    return f"שלב {i + 1} מתוך {total}: {st.he}"


def make_skill(features: feat.Features, gstate: GuideState, registry=None, *, opener=None,
               suggester: Suggester | None = None):
    """Voice/agent tools over the guides. opener: async (page) -> url, opens the dashboard on a page."""
    from pydantic import BaseModel, Field

    from ..skills import Skill, Tool, ToolResult

    class NameArgs(BaseModel):
        name: str = Field(..., description="Feature or guide name in Hebrew or English, e.g. 'google', 'מוזיקה', "
                                           "'camera', 'wake word'")

    class NextArgs(BaseModel):
        name: str = Field(..., description="Feature or guide name")
        done: bool = Field(False, description="true when the user says the current step is done / 'next'")

    class StepArgs(BaseModel):
        name: str = Field(..., description="Feature or guide name")
        step: int = Field(0, description="step number (1-based); 0 = the current step")

    class SnoozeArgs(BaseModel):
        name: str = Field(..., description="Guide name, or 'all'")
        days: float = Field(7, description="days to wait before suggesting it again; 0 = never again")

    class OnArgs(BaseModel):
        on: bool = Field(False, description="false = stop suggesting setup steps; true = allow again")

    def unknown(name: str) -> ToolResult:
        return ToolResult(False, f"לא מצאתי מדריך בשם {name}.", {"guides": list(load())}, "GUIDE_UNKNOWN")

    def current(g: Guide) -> int:
        """The first step not done: its check fails, or (no check) the user has not confirmed it yet."""
        p = gstate.progress(g.id)
        for i, st in enumerate(g.steps):
            if st.check and st.check != "test":
                if not _check(st.check, features)[0]:
                    return i
            elif i >= p:
                return i
        return len(g.steps)

    class HelpSkill(Skill):
        name = "help"
        description = "Guides for every feature: what it is, is it set up, how to set it up step by step"

        def tools(self) -> list[Tool]:
            async def explain(a: NameArgs):
                g = find(a.name)
                if g is None:
                    return unknown(a.name)
                d = full(g, features, gstate.progress(g.id))
                steps = " ".join(f"{s['n']}. {s['he']}" for s in d["steps"])
                msg = (f"{g.title_he}: {g.what_he} מצב: {_state_he(d['state'])}"
                       + (f" ({d['reason']})" if d["reason"] else "") + f". שלבים: {steps}")
                return ToolResult(True, msg, {"guide": g.id, "state": d["state"], "reason": d["reason"],
                                              "what_en": g.what_en, "say": d["say"][:3], "expect_he": g.expect_he,
                                              "cost_he": g.cost_he, "privacy_he": g.privacy_he,
                                              "steps": [s["en"] for s in d["steps"]],
                                              "dashboard": f"#/guide/{g.id}"})

            async def not_set_up(_):
                items = [summary(g, features) for g in load().values()]
                need = [s for s in items if s["state"] in ("setup", "partly")]
                todo = [c for c in checklist(features) if not c["done"]]
                names = "; ".join(f"{s['title_he']} ({s['reason']})" for s in need) or "הכול מוגדר"
                return ToolResult(True, f"צריך הגדרה: {names}.", {
                    "needs_setup": [{k: s[k] for k in ("id", "title_he", "title_en", "reason")} for s in need],
                    "first_steps": [{k: c[k] for k in ("id", "title_he", "done", "first_step_he")} for c in todo],
                    "off_by_choice": [s["id"] for s in items if s["state"] == "off"]})

            async def next_step(a: NextArgs):
                g = find(a.name)
                if g is None:
                    return unknown(a.name)
                total = len(g.steps)
                i = current(g)
                shown = gstate.read()["shown"].get(g.id)
                if a.done and isinstance(shown, int) and 0 <= shown < total:
                    i = shown                       # "done" means the step he read out last, not the next open one
                if a.done and i < total:
                    st = g.steps[i]
                    if st.check and st.check != "test":
                        ok, why = _check(st.check, features)
                        if not ok:
                            return ToolResult(True, f"עוד לא: {why}. {_step_text(g, i, total)}",
                                              {"guide": g.id, "step": i + 1, "total": total, "passed": False,
                                               "why": why, "action": st.action}, "GUIDE_STEP_NOT_DONE")
                    gstate.set_progress(g.id, max(gstate.progress(g.id), i + 1))
                    i = current(g)
                if i >= total:
                    res = run_test(g, features) if (g.test or g.features) else {"ok": True, "he": ""}
                    gstate.update(lambda d: (d["progress"].__setitem__(g.id, total), d["shown"].pop(g.id, None)))
                    msg = ("סיימנו. " + res["he"]) if res["ok"] else ("כל השלבים עברו, אבל הבדיקה עוד לא: " + res["he"])
                    say = f" נסו לומר: \"{g.say[0][0]}\"" if g.say and res["ok"] else ""
                    return ToolResult(True, msg + say, {"guide": g.id, "finished": True, "test_ok": res["ok"],
                                                        "test": res.get("en", "")})
                st = g.steps[i]
                gstate.update(lambda d: d["shown"].__setitem__(g.id, i))
                return ToolResult(True, _step_text(g, i, total) + " תגידו לי כשסיימתם.",
                                  {"guide": g.id, "step": i + 1, "total": total, "step_en": st.en,
                                   "action": st.action, "can_run": _can_run(st.action),
                                   "dashboard": f"#/guide/{g.id}",
                                   "do": "read this ONE step, then wait; when the user says done/next call "
                                         "setup_next_step with done=true"})

            async def run_step(a: StepArgs):
                g = find(a.name)
                if g is None:
                    return unknown(a.name)
                i = (a.step - 1) if a.step > 0 else current(g)
                if not 0 <= i < len(g.steps):
                    return ToolResult(False, "אין שלב כזה.", {"guide": g.id, "total": len(g.steps)}, "GUIDE_NO_STEP")
                return await run_action(g, g.steps[i], i)

            async def run_action(g: Guide, st: Step, i: int) -> ToolResult:
                kind, _, arg = st.action.partition(":")
                if kind == "feature_on" and arg in feat.CATALOG:
                    f0 = feat.CATALOG[arg]
                    if features.enabled(arg):
                        return ToolResult(True, f"{f0.title_he} כבר פועלת.", features.status(arg))
                    if f0.risk in ("medium", "high"):
                        confirm = getattr(registry, "confirm", None)
                        ok = await confirm(f"להפעיל את {f0.title_he}? / Turn on {f0.title_en}?") if confirm else False
                        if not ok:
                            features.request(arg, by="voice")
                            return ToolResult(False, f"{f0.title_he} צריכה אישור על המסך או בלוח הבקרה.",
                                              {"guide": g.id, "feature": arg, "risk": f0.risk}, "FEATURE_NEEDS_APPROVAL")
                    st_ = features.set(arg, True, by="voice")
                    msg = f"הפעלתי את {f0.title_he}." + ("" if st_["available"] else f" אבל עוד חסר: {st_['reason']}")
                    return ToolResult(True, msg, {"guide": g.id, "step": i + 1, **st_})
                if kind == "set_budget":
                    usd = set_budget(features.home, arg)
                    return ToolResult(True, f"קבעתי תקציב API חודשי של {usd:g} דולר. אתריע ב-75, 90 ו-100 אחוז.",
                                      {"guide": g.id, "api_monthly_budget_usd": usd})
                if kind == "test":
                    res = run_test(g, features)
                    return ToolResult(res["ok"], res["he"], {"guide": g.id, "test_ok": res["ok"], "en": res["en"]},
                                      "ok" if res["ok"] else "GUIDE_TEST_FAILED")
                if kind == "open":
                    return await open_page(arg or f"#/guide/{g.id}")
                if kind == "command":
                    return ToolResult(False, f"את השלב הזה עושים במחשב: מקלידים בטרמינל {arg}. אפשר גם לפתוח את "
                                             f"המדריך בלוח הבקרה.", {"guide": g.id, "command": arg,
                                                                      "dashboard": f"#/guide/{g.id}"},
                                      "GUIDE_STEP_MANUAL")
                return ToolResult(False, f"את השלב הזה עושים בעצמכם: {st.he}", {"guide": g.id, "step": i + 1},
                                  "GUIDE_STEP_MANUAL")

            async def open_page(page: str) -> ToolResult:
                if opener is None:
                    return ToolResult(False, "אין לי איך לפתוח את לוח הבקרה כאן.", {"page": page}, "DASHBOARD_FAILED")
                try:
                    url = await opener(page)
                except Exception as e:
                    return ToolResult(False, "לא הצלחתי לפתוח את לוח הבקרה.", {"error": type(e).__name__},
                                      "DASHBOARD_FAILED")
                return ToolResult(True, "פתחתי את המדריך בלוח הבקרה.", {"url": url.split("?", 1)[0] + page})

            async def open_guide(a: NameArgs):
                g = find(a.name)
                return await open_page(f"#/guide/{g.id}" if g else "#/guides")

            async def snooze(a: SnoozeArgs):
                if a.name.strip().lower() in ("all", "הכול", "הכל"):
                    return await stop(OnArgs(on=False))
                g = find(a.name)
                if g is None:
                    return unknown(a.name)
                if a.days <= 0:
                    gstate.dismiss(g.id)
                    return ToolResult(True, f"בסדר, לא אציע שוב את {g.title_he}.", {"guide": g.id, "dismissed": True})
                gstate.snooze(g.id, a.days)
                return ToolResult(True, f"בסדר, אזכיר שוב בעוד {a.days:g} ימים.", {"guide": g.id, "days": a.days})

            async def stop(a: OnArgs):
                features.set("proactive_help", bool(a.on), by="voice")     # OFF always allowed; ON is low risk
                return ToolResult(True, "בסדר, לא אציע יותר הגדרות מעצמי." if not a.on else "אחזור להציע מדי פעם.",
                                  {"proactive_help": bool(a.on)})

            return [
                Tool("explain_feature", "Explain one of my features (what it is, is it on and why not, the setup "
                                        "steps, what to say). Use for 'how do I connect Google / turn on the "
                                        "camera / what is X'.", explain, NameArgs, permission="read"),
                Tool("whats_not_set_up", "Which of my features still need setup, and the first steps to do.",
                     not_set_up, permission="read"),
                Tool("setup_next_step", "Walk the user through setting up a feature ONE step at a time. Call with "
                                        "done=true when the user says the step is done; it checks the result.",
                     next_step, NextArgs, permission="read"),
                Tool("run_guide_step", "Do a setup step for the user when it is safe (switch a low-risk feature on, "
                                       "run the check, open the page). Risky switches ask on the screen.",
                     run_step, StepArgs, permission="action", timeout_s=30),
                Tool("open_guide", "Open the dashboard guide page of a feature in the browser on this computer.",
                     open_guide, NameArgs, permission="action"),
                Tool("setup_snooze", "The user does not want this setup suggestion now: wait N days (0 = never).",
                     snooze, SnoozeArgs, permission="action"),
                Tool("stop_suggestions", "The user says 'stop suggesting' (or allows it again): my proactive setup "
                                         "suggestions switch.", stop, OnArgs, permission="action"),
            ]

    return HelpSkill()


HELP_ACTIONS = {"run_guide_step", "open_guide", "setup_snooze", "stop_suggestions"}


def _can_run(action: str) -> bool:
    return action.partition(":")[0] in ("feature_on", "test", "open", "set_budget")


def set_budget(home: Path | None, usd: str | float) -> float:
    """The 'Monthly API budget' step: only changes when JARVES alerts (75/90/100%), reversible on the Usage page."""
    from ..usage import set_settings
    if home is None:
        raise ValueError("no data folder")
    return float(set_settings(home, {"api_monthly_budget_usd": float(usd)})["api_monthly_budget_usd"])


def _state_he(s: str) -> str:
    return {"on": "פועל", "setup": "צריך הגדרה", "off": "כבוי", "partly": "חלקית", "planned": "עוד לא קיים",
            "missing": "לא בגרסה הזאת", "info": "מידע"}.get(s, s)


# ---------------------------------------------------------------------- Core wiring
def attach(core) -> "Helper":
    """Called once from Core.start after every Skill is registered: help Skill, setup hints, suggestions."""
    h = Helper(core)
    return h


class Helper:
    def __init__(self, core) -> None:
        self.core = core
        self.state = GuideState(core.home)
        self.suggester = Suggester(core.features, self.state, quiet=self._quiet, failing=self._failing)
        reg = core.registry

        async def opener(page: str) -> str:
            return await core.open_dashboard(page)
        reg.allowed_actions |= HELP_ACTIONS
        reg.register(make_skill(core.features, self.state, reg, opener=opener, suggester=self.suggester))
        reg.setup_hint = self.hint
        if getattr(core, "agent", None) is not None:
            augment_instructions(core.agent, core.features)

    def _quiet(self) -> bool:
        ops = getattr(self.core, "ops", None)
        eng = getattr(ops, "alerts", None)
        return bool(eng.quiet_now()) if eng is not None else False

    def _failing(self) -> set[str]:
        """Guides of tools that the self-diagnosis saw failing (open issues tool_fail:<tool>)."""
        hist = getattr(self.core, "history", None)
        if hist is None:
            return set()
        out = set()
        try:
            issues = getattr(hist, "store", hist).issues("open")       # StoreSink -> Store
        except Exception:
            return out
        reg = self.core.registry
        for i in issues:
            fp = str(i.get("fingerprint") or "")
            if fp.startswith("tool_fail:"):
                tool = fp.split(":", 1)[1]
                st = reg._tools.get(tool)
                fid = self.core.features.owner(st[0].name if st else "", tool)
                g = for_feature(fid) if fid else None
                if g is not None:
                    out.add(g.id)
        return out

    def hint(self, skill: str, tool: str, res) -> dict | None:
        """SkillRegistry hook: a setup hint for FEATURE_OFF and for error codes a guide owns."""
        if res.ok:
            return None
        if res.code == "FEATURE_OFF":
            return setup_hint(self.core.features.owner(skill, tool), self.core.features)
        g = guide_for_code(res.code)
        if g is not None and g.features:
            return setup_hint(g.features[0], self.core.features, code=res.code)
        return None

    def attach_live(self, live) -> None:
        attach_live(live, self.core.features, self.suggester)


# ---------------------------------------------------------------------- dashboard
def _ctx(ctx) -> tuple[feat.Features, GuideState]:
    f = feat._ctx_features(ctx)
    gs = getattr(ctx, "_guides_state", None)
    if gs is None:
        gs = ctx._guides_state = GuideState(ctx.home)
    return f, gs


def api_guides(ctx, m, body):
    f, gs = _ctx(ctx)
    items = [summary(g, f) for g in load().values()]
    d = gs.read()
    return 200, {"guides": items, "checklist": checklist(f),
                 "setup_needed": sum(1 for s in items if s["setup_needed"]),
                 "help": {"proactive_help": f.enabled("proactive_help") if "proactive_help" in feat.CATALOG else None,
                          "snoozed": {k: v for k, v in d["snoozed"].items() if v > time.time()},
                          "dismissed": list(d["dismissed"])}}


def api_guide(ctx, m, body):
    f, gs = _ctx(ctx)
    g = load().get(m.group(1))
    if g is None:
        return 404, {"error": "no such guide"}
    return 200, full(g, f, gs.progress(g.id))


def api_guide_test(ctx, m, body):
    f, _ = _ctx(ctx)
    g = load().get(m.group(1))
    if g is None:
        return 404, {"error": "no such guide"}
    return 200, run_test(g, f)


def api_guide_step(ctx, m, body):
    """Dashboard step button: the dashboard IS the user's approval (high-risk switches confirm in the page)."""
    f, gs = _ctx(ctx)
    g = load().get(m.group(1))
    if g is None:
        return 404, {"error": "no such guide"}
    i = int(m.group(2)) - 1
    if not 0 <= i < len(g.steps):
        return 404, {"error": "no such step"}
    st = g.steps[i]
    kind, _, arg = st.action.partition(":")
    if (body or {}).get("done"):
        gs.set_progress(g.id, max(gs.progress(g.id), i + 1))
        return 200, {"done": True, "progress": gs.progress(g.id)}
    if kind == "feature_on" and arg in feat.CATALOG:
        return 200, {"feature": f.set(arg, True, by="dashboard")}
    if kind == "set_budget":
        return 200, {"api_monthly_budget_usd": set_budget(ctx.home, (body or {}).get("usd") or arg)}
    if kind == "test":
        return 200, {"test": run_test(g, f)}
    return 400, {"error": "this step is done by you (see the text)"}


def api_help(ctx, m, body):
    f, gs = _ctx(ctx)
    b = body or {}
    g = load().get(str(b.get("guide") or ""))
    op = b.get("op")
    if op == "reset":
        gs.reset()
    elif op == "snooze" and g is not None:
        gs.snooze(g.id, float(b.get("days") or 7))
    elif op == "dismiss" and g is not None:
        gs.dismiss(g.id)
    elif op == "unsnooze" and g is not None:
        gs.update(lambda d: (d["snoozed"].pop(g.id, None), d["dismissed"].pop(g.id, None)))
    else:
        return 400, {"error": "op must be reset, snooze, dismiss or unsnooze (with guide)"}
    return 200, {"ok": True}


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/guides", api_guides)
    register_route("GET", r"/api/guides/([a-z0-9_]+)", api_guide)
    register_route("POST", r"/api/guides/([a-z0-9_]+)/test", api_guide_test)
    register_route("POST", r"/api/guides/([a-z0-9_]+)/step/(\d+)", api_guide_step)
    register_route("POST", r"/api/guides-help", api_help)
