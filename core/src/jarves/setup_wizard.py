"""First-run setup wizard (`jarvis setup`; Issue #17 SW-V3 "the installer installs all add-ons... choose what to
enable, choose the name... everything").

One flow, three ways to drive it:
- interactive    `jarvis setup` (the installer's last page and the first `jarvis` run via `setup --if-needed`):
                 Hebrew first (English on request), Enter always takes the recommended/current answer.
- answers file   `jarvis setup --answers answers.json` (silent installs: JARVES-Setup.exe /VERYSILENT
                 /SETUPANSWERS=answers.json). Never contains secrets: an API key is taken from the environment only
                 when `api_key_from_env` is true; a key-like value in the file is refused.
- defaults       `jarvis setup --defaults`: every recommended answer, no prompts.

Steps (STEPS): language -> connection (+ key / ChatGPT sign-in) -> names (display, spoken, how to address the user)
-> wake phrase (honest: "Hey Jarvis" is the built-in one; a custom phrase needs `jarvis wake train`) -> voice
(identity.voice_catalog) -> add-ons checklist (every feature in jarves.features, grouped, with explanation,
cost/privacy note from its guide and a live availability probe) -> privacy mode -> monthly API budget -> Google
(offer `jarvis google start` now or later) -> summary + "what to do next" (from the guides).

Where things are written (one source of truth each, never config.toml for secrets): switches -> jarves.features
(features.json, by="setup"); names / wake / voice -> identity.json (+ memory.db override for the GPT-Live voice);
"how to address me" -> one managed line in workspace USER.md; connection -> config.toml [ai] connection; privacy
-> jarves.privacy (flag file + config.toml log.privacy); budget -> usage settings; key -> secrets.env.
Idempotent: a value equal to the current one is not written again. Re-runnable: every default is the CURRENT
choice, so `jarvis setup` again only changes what you change. Resumable: progress is saved after every step in
<state_dir>/setup_state.json; an interrupted run continues where it stopped (`--restart` begins again).
Safe defaults on the first run: camera, face recognition, camera-when-away, sending mail, calendar writes and the
LAN dashboard stay OFF (SAFE_OFF); a high-risk add-on is switched on only after an explicit "yes".

`check_addons()` (`jarvis setup --check-addons`): is every add-on's code, model and data actually in this install
(imports, YuNet, the wake models, tzdata, dashboard files...)? Used by packaging/smoke_windows.ps1 on the installed
app. Never opens a camera or microphone and never calls the network.

The dashboard Setup page (#/setup, dashboard_static/setup.js) shows the same groups (GET /api/setup) and reuses the
Features switches (POST /api/features/<id>), the Identity & voice page and the Guides.
"""
from __future__ import annotations

import importlib
import importlib.resources
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from . import features as feat
from . import hebrew, logs

STATE_FILE = "setup_state.json"
PEAR_URL = "https://github.com/pear-devs/pear-desktop/releases"
STEPS = ("language", "connection", "names", "wake", "voice", "addons", "privacy", "budget", "google", "connect")
# Modules that register add-ons in jarves.features at import (the dashboard imports the same ones).
ADDON_MODULES = ("jarves.telegram_channel", "jarves.instagram_channel", "jarves.whatsapp", "jarves.briefing", "jarves.homeassistant",
                 "jarves.appearance", "jarves.backup", "jarves.usage", "jarves.updates", "jarves.runtime",
                 "jarves.operations", "jarves.guides", "jarves.skills.generated", "jarves.browser", "jarves.profiles",
                 "jarves.selfcheck", "jarves.connect", "jarves.skills.helper", "jarves.context_trace",
                 "jarves.recall", "jarves.diag.service", "jarves.whatsapp_bridge", "jarves.orchestrator", "jarves.dialing")
# Privacy-sensitive or outward-acting: OFF on the first run whatever else says; ON only after an explicit yes.
SAFE_OFF = frozenset({"camera", "face_recognition", "camera_when_away", "gmail_send", "calendar_write",
                      "dashboard_lan", "mobile_access", "browser", "telegram", "home_assistant", "instagram", "helper",
                      "helper_readonly_auto", "whatsapp_bridge", "whatsapp_send", "agent_orchestrator", "assisted_dialing"})
# Registered and grouped (so coverage checks see them) but never offered by the wizard: the owner decided (20 Oct)
# that the Helper asks for EVERY command; the read-only allow-list exists only as `jarvis features on ...`.
WIZARD_HIDDEN = frozenset({"helper_readonly_auto",
                           "whatsapp_send"})   # SW-V4 F: send mode only after 14 read-only days: never a wizard offer
GOOGLE_FEATURES = ("google_calendar", "calendar_write", "gmail_read", "gmail_send", "google_contacts")
# (id, Hebrew, English, feature ids). A feature missing here lands in "other" (never hidden); a test demands
# that every registered feature is listed.
GROUPS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("core", "בסיס", "Core", ("web_search", "background_tasks", "memory_curator", "memory_llm_pass",
                             "diagnosis_llm_pass", "conversation_recall", "self_check", "self_diagnostics_2", "self_tests", "guided_setup", "workspace_agent_edit", "assistant_identity", "skill_creation",
                             "proactive_help", "usage_monitor", "usage_admin_key", "appearance", "background_core", "display_control",
                             "tray_icon", "autostart")),
    ("voice", "קול", "Voice", ("wake_word", "greeting", "chimes", "voice_change", "client_delegation")),
    ("music", "מוזיקה", "Music", ("music",)),
    ("google", "גוגל", "Google", GOOGLE_FEATURES),
    ("camera", "מצלמה ופרטיות", "Camera & privacy", ("camera", "camera_preview", "face_recognition", "camera_when_away")),
    ("browser", "דפדפן", "Browser", ("browser",)),
    ("automation", "אוטומציה", "Automation", ("scheduler", "reports", "timers", "lists", "notes", "files", "proactive_alerts",
                                              "autonomy", "morning_briefing", "goals", "adaptive_memory")),
    ("channels", "ערוצים ובית חכם", "Channels & smart home", ("telegram", "instagram", "whatsapp_handoff",
                                                                  "whatsapp_bridge", "whatsapp_send", "home_assistant")),
    ("phone", "שיחות טלפון", "Phone calls", ("assisted_dialing",)),
    ("dashboard", "לוח בקרה וטלפון", "Dashboard & phone", ("dashboard", "dashboard_lan", "mobile_access", "context_trace")),
    ("updates", "עדכונים וגיבוי", "Updates & backup", ("update_check", "backup")),
    ("profiles", "פרופילים", "Profiles", ("profiles",)),
    ("helper", "עזרה במחשב (פקודות והתקנות)", "Computer helper (commands & installs)", ("helper", "helper_readonly_auto")),
    ("agents", "סוכני קוד", "Coding agents", ("agent_orchestrator",)),
)
ANSWER_KEYS = {"language", "connection", "api_key_from_env", "display_name", "spoken_name", "address_me",
               "wake_model", "voice", "approve_voice_cost", "features", "privacy_mode", "monthly_budget_usd",
               "connect_google_now", "connect_now"}
ADDRESS_MARK = "איך לפנות אליי / How to address me:"
CONNECTIONS = (("chatgpt", "תוכנית ChatGPT שלי (בלי עלות נוספת, קול פשוט)", "My ChatGPT plan (no extra cost, simple voice)"),
               ("combined", "ChatGPT + מפתח API (הקול הכי טוב, הקול מחויב במפתח)",
                "ChatGPT plan + API key (best voice, voice billed to the key)"),
               ("api_key", "מפתח API בלבד (הכול הכי טוב, הכול מחויב במפתח)", "API key only (best everything, billed to the key)"))

T: dict[str, tuple[str, str]] = {
    "title": ("הגדרת JARVES", "JARVES setup"),
    "enter": ("Enter = הבחירה המומלצת / הנוכחית (בסוגריים).", "Enter = the recommended / current choice (in brackets)."),
    "resume": ("ההגדרה הקודמת נעצרה באמצע. להמשיך מאיפה שעצרנו?", "The last setup stopped half-way. Continue where it stopped?"),
    "lang": ("שפה / Language", "Language / שפה"),
    "conn": ("איך JARVES מתחבר ל-AI?", "How should JARVES connect to AI?"),
    "key_env_saved": ("מפתח ה-API נשמר מהסביבה (secrets.env, פרטי למשתמש שלך).",
                      "API key saved from the environment (secrets.env, private to your Windows user)."),
    "key_later": ("אין מפתח API שמור: הקול החי כבוי עד שתריץ  jarvis key", "No API key saved: live voice stays off until you run  jarvis key"),
    "login_now": ("להתחבר ל-ChatGPT עכשיו בדפדפן?", "Sign in to ChatGPT now in the browser?"),
    "login_later": ("ChatGPT לא מחובר. אחר כך:  jarvis chatgpt login", "ChatGPT is not connected. Later:  jarvis chatgpt login"),
    "names": ("השם של העוזר", "The assistant's name"),
    "display": ("שם כתוב (בלוח הבקרה ובחלונות)", "Written name (dashboard, windows)"),
    "spoken": ("איך הוא אומר את השם שלו בעברית", "How he says his own name in Hebrew"),
    "address": ("איך הוא יפנה אליך? (שם, לשון זכר/נקבה; ריק = לא לשמור)",
                "How should he address you? (name, he/she form; empty = nothing saved)"),
    "wake": ("מילת ההפעלה", "Wake phrase"),
    "wake_honest": ("המילה המובנית היחידה היא \"Hey Jarvis\" (עובדת מיד, במחשב בלבד, שום קול לא יוצא לפני שהיא נשמעת). "
                    "שינוי השם לא משנה את מילת ההפעלה. מילה משלך: מאמנים אותה במחשב הזה (כמה דקות, מקומי) עם\n"
                    "  jarvis wake train \"<המילה>\" --say \"<איך אומרים בעברית>\"\n"
                    "ואז בוחרים אותה כאן או בלוח הבקרה (שם וקול). מילה מאומנת עדיין לא נבדקה על Raspberry Pi.",
                    "The only built-in phrase is \"Hey Jarvis\" (works at once, on this PC only; no audio leaves before "
                    "it is heard). Renaming does not change it. Your own phrase is trained on this PC (a few minutes, "
                    "local) with\n  jarvis wake train \"<phrase>\" --say \"<Hebrew spelling>\"\nthen picked here or on the "
                    "dashboard (Identity & voice). Trained phrases are not verified on the Raspberry Pi yet."),
    "wake_train_later": ("לאמן מילה חדשה אחר כך (מדפיס את הפקודה)", "Train a new phrase later (prints the command)"),
    "voice": ("הקול", "Voice"),
    "voice_note": ("אין דוגמת שמע: OpenAI לא מפרסם דגימות, ו-JARVES לא מזייף. הקול משמש את הקול החי (צריך מפתח API).",
                   "No audio preview: OpenAI publishes no samples and JARVES does not fake one. Used by live voice "
                   "(needs the API key)."),
    "voice_other": ("קולות של מודל אחר (משנה מחיר)", "Voices of another model (changes the price)"),
    "voice_cost": ("זה משנה את המחיר. לאשר?", "This changes the price. Approve?"),
    "addons": ("תוספים", "Add-ons"),
    "addons_rec": ("להשתמש בבחירות המומלצות לכל התוספים? (לא = לבחור אחד-אחד)",
                   "Use the recommended choices for all add-ons? (no = choose one by one)"),
    "risk_yes": ("זו יכולת רגישה. להפעיל? הקלד כן / yes", "This is a sensitive feature. Turn it on? Type yes / כן"),
    "ready": ("זמין", "available"),
    "needs_parent": ("כבוי כי צריך את", "off because it needs"),
    "privacy": ("מצב פרטיות: לא לשמור את המילים של השיחות (רק אורך ותזמון)?",
                "Privacy mode: do not keep the words of conversations (only lengths and timings)?"),
    "privacy_note": ("כבוי = הכול נשמר במחשב הזה בלבד (עוזר לאבחון עצמי). שום דבר לא נשלח החוצה.",
                     "Off = everything is kept on this PC only (helps self-diagnosis). Nothing is uploaded."),
    "budget": ("תקציב API חודשי בדולרים (התראות ב-75/90/100%; ריק = בלי)", "Monthly API budget in USD (alerts at 75/90/100%; empty = none)"),
    "google_now": ("גוגל עוד לא מחובר. לפתוח עכשיו את  jarvis google start  (כ-10 דקות, צעד אחר צעד)?",
                   "Google is not connected yet. Open  jarvis google start  now (about 10 minutes, step by step)?"),
    "google_later": ("לחיבור גוגל אחר כך:  jarvis google start", "Connect Google later:  jarvis google start"),
    "connect_now": ("להריץ עכשיו  jarvis connect ? הוא בודק מה מחובר (גוגל, טלגרם, Pear, מילת ההפעלה) ומדריך רק במה שחסר.",
                    "Run  jarvis connect  now? It checks what is connected (Google, Telegram, Pear, wake word) and "
                    "guides you through only what is missing."),
    "connect_later": ("לחיבור כל החשבונות בהדרכה:  jarvis connect", "Connect every account with guidance:  jarvis connect"),
    "summary": ("סיכום", "Summary"),
    "next": ("מה לעשות עכשיו", "What to do next"),
    "change": ("לשנות הכול בכל זמן:  jarvis setup  או בלוח הבקרה: הגדרה ותוספים (#/setup)",
               "Change anything later:  jarvis setup  or the dashboard: Setup & add-ons (#/setup)"),
    "done": ("ההגדרה הושלמה.", "Setup complete."),
    "stopped": ("ההגדרה נעצרה. ההתקדמות נשמרה; הריצו  jarvis setup  כדי להמשיך.",
                "Setup stopped. Progress is saved; run  jarvis setup  to continue."),
    "invalid": ("לא הבנתי. נסו שוב.", "Not understood. Try again."),
    "yes_no": ("כ/ל", "y/n"),
    "on": ("פועל", "on"), "off": ("כבוי", "off"),
    "pear": ("Pear Desktop (תוכנה נפרדת, חינם) להורדה:", "Get Pear Desktop (separate free app):"),
}


class Abort(Exception):
    """The user stopped (Ctrl+C, end of input after a wrong answer): progress is kept, nothing half-written."""


class AnswerError(ValueError):
    """An answers file value that cannot be applied (exit code 2, nothing after it is applied)."""


def load_addons() -> list[str]:
    """Import every module that registers add-ons; returns those that failed (= not shipped in this install)."""
    missing = []
    for name in ADDON_MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:           # a broken optional module must not stop setup
            missing.append(f"{name} ({type(e).__name__})")
    return missing


def groups() -> list[tuple[str, str, str, list[str]]]:
    """GROUPS restricted to registered features, plus "other" for anything registered but not grouped."""
    load_addons()
    seen, out = set(), []
    for gid, he, en, ids in GROUPS:
        have = [i for i in ids if i in feat.CATALOG and i not in WIZARD_HIDDEN]
        seen.update(ids)
        if have:
            out.append((gid, he, en, have))
    rest = [i for i in feat.CATALOG if i not in seen]
    if rest:
        out.append(("other", "אחר", "Other", rest))
    return out


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, d: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_state(home: Path) -> dict:
    return _read_json(home / STATE_FILE)


def first_run(home: Path) -> bool:
    """No setup was ever finished (neither this wizard nor the older connection-only one)."""
    return not (home / "setup_done").exists() and not read_state(home).get("finished_at")


def recommended(fid: str, f: feat.Features, first: bool) -> bool:
    """The default the wizard offers: the current switch; on the first run SAFE_OFF features are always off."""
    return False if first and fid in SAFE_OFF else f.enabled(fid)


def _guide_notes(fid: str) -> dict:
    from . import guides
    g = guides.for_feature(fid)
    if g is None:
        return {"guide": None}
    return {"guide": g.id, "cost_he": g.cost_he, "cost_en": g.cost_en, "privacy_he": g.privacy_he,
            "privacy_en": g.privacy_en}


def _he(why: str) -> str:
    """The Hebrew form of a probe reason when there is one (SW-V4 K: no English inside the Hebrew hint)."""
    from . import hebrew
    return hebrew.he(why) or why


def hint(fid: str, ok: bool, why: str) -> dict:
    """Extra, specific help for the availability line (Pear link, Google start, camera)."""
    if ok:
        return {}
    if fid == "music":
        return {"link": PEAR_URL, "he": "Pear Desktop לא פועל או לא מותקן (תוכנה נפרדת) - " + PEAR_URL,
                "en": "Pear Desktop is not running or not installed (separate app) - " + PEAR_URL}
    if fid in GOOGLE_FEATURES:
        return {"command": "jarvis google start", "he": "צריך לחבר את גוגל:  jarvis google start",
                "en": "Google must be connected:  jarvis google start"}
    if fid in ("camera", "camera_preview", "face_recognition", "camera_when_away"):
        return {"he": "מצלמה: " + _he(why), "en": "Camera: " + why}
    if fid == "browser":
        return {"command": "jarvis features on browser", "he": "דפדפן: " + _he(why), "en": "Browser: " + why}
    if fid == "telegram":
        return {"command": "jarvis telegram start", "he": "צריך להגדיר בוט טלגרם:  jarvis telegram start",
                "en": "Set up your Telegram bot:  jarvis telegram start"}
    if fid == "instagram":
        return {"command": "jarvis instagram start", "he": "צריך לחבר חשבון אינסטגרם עסקי:  jarvis instagram start",
                "en": "Connect your Instagram Business account:  jarvis instagram start"}
    if fid == "home_assistant":
        return {"command": "jarvis home start", "he": "צריך לחבר את Home Assistant:  jarvis home start",
                "en": "Connect Home Assistant:  jarvis home start"}
    if fid == "whatsapp_bridge":
        return {"command": "jarvis whatsapp link", "he": "צריך לקשר את וואטסאפ (מכשיר מקושר, קריאה בלבד קודם):  "
                "jarvis whatsapp link", "en": "Link WhatsApp (a linked device, read-only first):  jarvis whatsapp link"}
    if fid == "whatsapp_send":
        return {"command": "jarvis whatsapp mode send", "he": "שליחה רק אחרי 14 ימי קריאה שקטים ואישור שלך:  "
                "jarvis whatsapp mode send", "en": "Send only after 14 quiet read-only days and your go:  "
                "jarvis whatsapp mode send"}
    return {}


def view(cfg, home: Path, f: feat.Features | None = None) -> dict:
    """Everything the Setup page / a test needs: groups with live status, notes, recommendation and hints."""
    if f is None:
        f = feat.Features(cfg, home)
    first = first_run(home)
    out = []
    for gid, he, en, ids in groups():
        rows = []
        for fid in ids:
            s = f.status(fid)
            rows.append({**{k: s[k] for k in ("id", "title_he", "title_en", "about", "about_he", "risk", "enabled",
                                              "available", "reason", "reason_he", "active", "needs", "applies", "source")
                                if k in s},
                         "recommended": recommended(fid, f, first), "safe_off": fid in SAFE_OFF,
                         "hint": hint(fid, s["available"], s["reason"]), **_guide_notes(fid)})
        out.append({"id": gid, "title_he": he, "title_en": en, "features": rows})
    st = read_state(home)
    return {"groups": out, "first_run": first, "language": st.get("language", "he"),
            "finished_at": st.get("finished_at"), "connection": cfg.ai.connection, "safe_off": sorted(SAFE_OFF),
            "next_steps": next_steps(f), "command": "jarvis setup"}


def next_steps(f: feat.Features) -> list[dict]:
    """'What to do next': each enabled-but-not-ready add-on's guide with its first unfinished step, then the
    guides' first-run checklist. One entry per guide."""
    from . import guides
    out, seen = [], set()
    for fid in feat.CATALOG:
        if not f.enabled(fid) or f.available(fid)[0]:
            continue
        g = guides.for_feature(fid)
        if g is None or g.id in seen:
            continue
        seen.add(g.id)
        h = guides.setup_hint(fid, f) or {}
        out.append({"guide": g.id, "title_he": g.title_he, "title_en": g.title_en, "reason": f.reason(fid),
                    "reason_he": hebrew.he(f.reason(fid)) or "",
                    "step_he": h.get("next_step_he", ""), "step_en": h.get("next_step_en", ""),
                    "dashboard": f"#/guide/{g.id}"})
    for c in guides.checklist(f):
        if not c["done"] and c["id"] not in seen and c["state"] != "off":
            seen.add(c["id"])
            out.append({"guide": c["id"], "title_he": c["title_he"], "title_en": c["title_en"], "reason": c["reason"],
                        "reason_he": c.get("reason_he", ""),
                        "step_he": c["first_step_he"], "step_en": c["first_step_en"], "dashboard": f"#/guide/{c['id']}"})
    return out


# ---------------------------------------------------------------------- console I/O
class ConsoleIO:
    """input()/getpass with a scripted fallback: end of input = Enter (the default) unless the last answer was
    invalid, then Abort. Tests pass `lines`."""

    def __init__(self, lines: list[str] | None = None, write: Callable[[str], None] | None = None) -> None:
        self.lines = list(lines) if lines is not None else None
        self.write = write or (lambda s: print(s, flush=True))

    def out(self, text: str = "") -> None:
        self.write(text)

    def read(self, prompt: str) -> str | None:
        if self.lines is not None:
            self.write(prompt)
            return self.lines.pop(0) if self.lines else None
        try:
            return input(prompt)
        except EOFError:
            return None


class Wizard:
    def __init__(self, cfg, home: Path, *, config_path: Path, io: ConsoleIO | None = None,
                 answers: dict | None = None, defaults: bool = False, lang: str | None = None,
                 features: feat.Features | None = None, actions: dict[str, Callable] | None = None,
                 restart: bool = False) -> None:
        self.cfg, self.home, self.config_path = cfg, Path(home), Path(config_path)
        self.io = io or ConsoleIO()
        self.scripted = answers is not None or defaults
        self.answers = dict(answers or {})
        bad = set(self.answers) - ANSWER_KEYS
        if bad:
            raise AnswerError(f"unknown answer key(s): {', '.join(sorted(bad))}; known: {', '.join(sorted(ANSWER_KEYS))}")
        if _has_secret(self.answers):
            raise AnswerError("the answers file contains something that looks like a key; secrets never go there "
                              "(use api_key_from_env, or run jarvis key)")
        load_addons()
        self.f = features or _cli_features(cfg, self.home)
        self.first = first_run(self.home)
        self.state = {} if restart else read_state(self.home)
        picked = lang or self.answers.get("language") or self.state.get("language")
        from . import clilang
        mode = clilang.current()
        self.both = not picked and mode == "both"            # JARVES_CLI_LANG / ui.cli_language = both: print both
        self.lang = picked or ("he" if mode in ("he", "both") else "en")
        self.actions = {"ask_key": _ask_key, "chatgpt_login": None, "google_start": None, "prepare": _prepare,
                        "autostart": _autostart, **(actions or {})}
        self.notes: list[str] = []          # "do later" lines collected for the summary
        self.changed: list[str] = []        # what this run changed (for the log and tests)
        self._memory = None

    # --- text + questions
    def t(self, key: str) -> str:
        he, en = T[key]
        return self.L(he, en)

    def L(self, he: str, en: str) -> str:
        if self.both:
            return f"{he}\n{en}"
        return he if self.lang == "he" else en

    def say(self, text: str = "") -> None:
        self.io.out(text)

    def _ask(self, prompt: str, parse: Callable[[str], Any], default: Any) -> Any:
        wrong = False
        for _ in range(5):
            raw = self.io.read(prompt)
            if raw is None:
                if wrong:
                    raise Abort()
                return default
            raw = raw.strip()
            if raw == "":
                return default
            try:
                return parse(raw)
            except ValueError:
                wrong = True
                self.say(self.t("invalid"))
        raise Abort()

    def yesno(self, key: str, prompt: str, default: bool) -> bool:
        if self.scripted:
            v = self.answers.get(key, default)
            if not isinstance(v, bool):
                raise AnswerError(f"{key} must be true or false")
            return v

        def parse(s: str) -> bool:
            s = s.lower()
            if s in ("y", "yes", "כ", "כן", "1", "on"):
                return True
            if s in ("n", "no", "ל", "לא", "0", "off"):
                return False
            raise ValueError(s)
        d = self.L("כ", "y") if default else self.L("ל", "n")
        return self._ask(f"{prompt} [{d}] ({self.t('yes_no')}): ", parse, default)

    def choose(self, key: str, prompt: str, options: list[tuple[Any, str]], default: Any) -> Any:
        values = [v for v, _ in options]
        if self.scripted:
            v = self.answers.get(key, default)
            if v not in values:
                raise AnswerError(f"{key} must be one of {values}")
            return v
        self.say(prompt)
        for i, (v, label) in enumerate(options, 1):
            self.say(f"  {i}  {label}" + ("   <-" if v == default else ""))
        di = values.index(default) + 1 if default in values else 1

        def parse(s: str):
            if s.isdigit() and 1 <= int(s) <= len(values):
                return values[int(s) - 1]
            if s in values:
                return s
            raise ValueError(s)
        return self._ask(f"[{di}]: ", parse, values[di - 1])

    def text(self, key: str, prompt: str, default: str, check: Callable[[str], str] | None = None) -> str:
        if self.scripted:
            v = self.answers.get(key, default)
            if not isinstance(v, str):
                raise AnswerError(f"{key} must be text")
            if check and v:
                try:
                    return check(v)
                except ValueError as e:
                    raise AnswerError(f"{key}: {e}") from e
            return v
        return self._ask(f"{prompt} [{default}]: ", (lambda s: check(s)) if check else (lambda s: s), default)

    # --- state
    def _save_state(self, done: list[str]) -> None:
        d = {**read_state(self.home), "version": 1, "language": self.lang, "completed": done, "updated_at": time.time()}
        _write_json(self.home / STATE_FILE, d)

    # --- the run
    def run(self) -> int:
        done = list(self.state.get("completed") or [])
        resumable = bool(done) and not self.state.get("finished_at") and not self.scripted
        self.say(f"== {T['title'][0]} / {T['title'][1]} ==" if self.both else f"== {self.t('title')} ==")
        if resumable and not self.yesno("resume", self.t("resume"), True):
            done = []
        elif not resumable:
            done = []
        try:
            for step in STEPS:
                if step in done:
                    continue
                getattr(self, "step_" + step)()
                done.append(step)
                self._save_state(done)
        except (Abort, KeyboardInterrupt):
            self.say("\n" + self.t("stopped"))
            logs.info("setup", "SETUP_STOPPED", "setup wizard stopped; progress kept", done=len(done))
            return 1
        finally:
            self._close()
        self.summary()
        st = read_state(self.home)
        st.update(completed=[], finished_at=time.time(), language=self.lang)
        _write_json(self.home / STATE_FILE, st)
        (self.home / "setup_done").write_text(self.cfg.ai.connection, encoding="utf-8")
        logs.info("setup", "SETUP_DONE", "setup wizard finished", changed=self.changed[:40],
                  mode="answers" if self.answers else "defaults" if self.scripted else "interactive")
        return 0

    # --- steps
    def step_language(self) -> None:
        self.lang = self.choose("language", "Language / שפה", [("he", "עברית"), ("en", "English")], self.lang)
        self.say(self.t("enter") if not self.scripted else "")

    def step_connection(self) -> None:
        from .chatgpt_auth import ChatGPTAuth
        from .cli import SPEND_LIMIT_ADVICE, set_connection
        from .config import openai_key_present, save_secret
        cur = self.cfg.ai.connection
        opts = [(v, self.L(he, en)) for v, he, en in CONNECTIONS]
        conn = self.choose("connection", "\n" + self.t("conn"), opts, cur)
        if conn != cur or not self.config_path.exists():
            set_connection(self.config_path, conn)
            self.cfg.ai.connection = conn
            self.changed.append("connection")
        if conn in ("combined", "api_key"):
            if not openai_key_present():
                env = os.environ.get("OPENAI_API_KEY", "").strip()
                if self.scripted and self.answers.get("api_key_from_env") and env.startswith("sk-"):
                    save_secret("OPENAI_API_KEY", env, self.home)
                    self.say(self.t("key_env_saved"))
                elif self.scripted or self.actions["ask_key"](self.home) != 0:
                    self.notes.append(self.t("key_later"))
            self.say(SPEND_LIMIT_ADVICE)
        if conn in ("chatgpt", "combined") and not ChatGPTAuth(self.home).status()["plan_usage"]:
            login = self.actions.get("chatgpt_login")
            if not self.scripted and login is not None and self.yesno("login", self.t("login_now"), True):
                if login() != 0:
                    self.notes.append(self.t("login_later"))
            else:
                self.notes.append(self.t("login_later"))

    def _close(self) -> None:
        if self._memory is not None:
            self._memory.close()
            self._memory = None

    def _identity(self):
        from .identity import Identity
        if self._memory is None:
            from .memory import MemoryStore
            self._memory = MemoryStore(self.home / "memory.db")
        return Identity(self.home, self.cfg, self._memory)

    def step_names(self) -> None:
        from .identity import IdentityError, _clean_name
        ident = self._identity()
        cur = ident.names()

        def check(v: str) -> str:
            try:
                return _clean_name(v, "name")
            except IdentityError as e:
                raise ValueError(str(e)) from e
        self.say("\n" + self.t("names"))
        display = self.text("display_name", self.t("display"), cur["display_name"], check)
        spoken = self.text("spoken_name", self.t("spoken"), cur["spoken_name"], check)
        if (display, spoken) != (cur["display_name"], cur["spoken_name"]):
            ident.set_names(display, spoken, by="setup")
            self.changed.append("names")
        old = current_address(self.home)
        addr = self.text("address_me", self.t("address"), old).strip()
        addr = "" if addr == "-" else addr          # "-" clears it
        if addr != old:
            set_address(self.home, addr)
            self.changed.append("address_me")

    def step_wake(self) -> None:
        from .voice.wake_train import list_models
        ident = self._identity()
        cur = ident.wake()
        self.say("\n" + self.t("wake") + f": \"{cur['phrase']}\"\n" + self.t("wake_honest"))
        models = list_models(self.home)
        opts: list[tuple[Any, str]] = [("hey_jarvis", "Hey Jarvis (" + self.L("מובנית", "built-in") + ")")]
        opts += [(m["name"], f"\"{m['phrase']}\" ({self.L('אומנה במחשב הזה', 'trained on this PC')})") for m in models]
        opts.append(("train_later", self.t("wake_train_later")))
        cur_id = "hey_jarvis" if cur["model"] == "hey_jarvis" else Path(cur["model"]).stem
        pick = self.choose("wake_model", "", opts, cur_id if any(v == cur_id for v, _ in opts) else "hey_jarvis")
        if pick == "train_later":
            self.notes.append('jarvis wake train "<phrase>" --say "<Hebrew spelling>"')
            return
        if pick == cur_id:
            return
        path = "hey_jarvis" if pick == "hey_jarvis" else next(m["path"] for m in models if m["name"] == pick)
        ident.set_wake(path, by="setup", only_in=None if path == "hey_jarvis" else self.home / "wake")
        self.changed.append("wake")

    def step_voice(self) -> None:
        from .identity import IdentityError, voice_catalog
        ident = self._identity()
        cur = ident.voice()
        cat = voice_catalog(self.cfg)
        mode = cur["mode"] if cur["mode"] in ("live", "realtime") else "live"
        same = [v for v in cat if v["mode"] == mode]
        price = same[0]["usd_per_minute"] if same else None
        self.say("\n" + self.t("voice") + f" ({same[0]['model'] if same else mode}"
                 + (f", ~${price}/min" if price else "") + ")\n" + self.t("voice_note"))
        opts = [(v["id"], f"{v['voice']:<10} {v['about']}") for v in same]
        others = [v for v in cat if v["mode"] != mode]
        if not self.scripted and others:
            opts.append(("other", self.t("voice_other")))
        current = cur["current"] or (same[0]["id"] if same else None)
        if self.scripted:
            opts += [(v["id"], v["voice"]) for v in others]
        pick = self.choose("voice", "", opts, current)
        if pick == "other":
            o = others[0]
            pick = self.choose("voice", f"{o['model']} (~${o['usd_per_minute']}/min, {o['cost_kind']})",
                               [(v["id"], f"{v['voice']:<10} {v['about']}") for v in others], others[0]["id"])
        if pick == current:
            return
        try:
            r = ident.choose_voice(pick, by="setup")
            if r.get("needs_approval"):
                ok = (self.answers.get("approve_voice_cost") is True) if self.scripted else \
                    self.yesno("approve_voice_cost", r["reason"] + "\n" + self.t("voice_cost"), False)
                if not ok:
                    self.notes.append(self.L("הקול לא הוחלף (שינוי מחיר לא אושר): ", "Voice not changed (price change "
                                             "not approved): ") + r["reason"])
                    return
                r = ident.choose_voice(pick, approve_cost=True, by="setup")
        except IdentityError as e:
            if self.scripted:
                raise AnswerError(f"voice: {e}") from e
            self.say(str(e))
            return
        if r.get("ok"):
            self.changed.append("voice")

    def _line(self, fid: str) -> str:
        s = self.f.status(fid)
        n = _guide_notes(fid)
        ok, why = s["available"], s["reason"]
        h = hint(fid, ok, why)
        title = s["title_he"] if self.lang == "he" else s["title_en"]
        parts = [f"- {title}: {s['about']}"]
        for k in ("cost", "privacy"):
            v = n.get(f"{k}_{self.lang}") or n.get(f"{k}_en")
            if v:
                parts.append(f"    {v}")
        parts.append("    " + (self.t("ready") if ok else (h.get(self.lang) or why)))
        return "\n".join(parts)

    def step_addons(self) -> None:
        self.say("\n== " + self.t("addons") + " ==")
        want: dict[str, bool] = {}
        for gid, he, en, ids in groups():
            for fid in ids:
                want[fid] = recommended(fid, self.f, self.first)
        if self.scripted:
            given = self.answers.get("features") or {}
            if not isinstance(given, dict):
                raise AnswerError("features must be an object {id: true|false}")
            for fid, on in given.items():
                if fid not in feat.CATALOG or fid in WIZARD_HIDDEN:
                    self.say(f"(feature {fid!r} not offered here; skipped)")
                    continue
                if not isinstance(on, bool):
                    raise AnswerError(f"features.{fid} must be true or false")
                want[fid] = on
        else:
            for gid, he, en, ids in groups():
                ons = [feat.CATALOG[i].title_he if self.lang == "he" else feat.CATALOG[i].title_en
                       for i in ids if want[i]]
                offs = [feat.CATALOG[i].title_he if self.lang == "he" else feat.CATALOG[i].title_en
                        for i in ids if not want[i]]
                self.say(f"{self.L(he, en)}: {self.t('on')}: {', '.join(ons) or '-'}; {self.t('off')}: {', '.join(offs) or '-'}")
            if not self.yesno("addons_rec", self.t("addons_rec"), True):
                for gid, he, en, ids in groups():
                    self.say(f"\n-- {self.L(he, en)} --")
                    for fid in ids:
                        parent = next((d for d in feat.CATALOG[fid].needs if d in want and not want[d]), None)
                        if parent:
                            want[fid] = False
                            self.say(f"- {feat.CATALOG[fid].title_en}: {self.t('needs_parent')} {feat.CATALOG[parent].title_en}")
                            continue
                        self.say(self._line(fid))
                        on = self.yesno(fid, "  " + self.t("on") + "?", want[fid])
                        if on and not want[fid] and feat.CATALOG[fid].risk == "high":
                            raw = (self.io.read("  " + self.t("risk_yes") + ": ") or "").strip().lower()
                            on = raw in ("yes", "כן")
                        want[fid] = on
        self._apply_features(want)

    def _apply_features(self, want: dict[str, bool]) -> None:
        for fid, on in want.items():
            if self.f.enabled(fid) == on:
                continue
            if on:
                ok, detail = self.actions["prepare"](fid, self.f, self.home)
                if not ok and detail:
                    self.notes.append(f"{fid}: {detail}")
            if fid == "autostart":              # the switch AND the OS entry, like `jarvis autostart on|off`
                try:
                    self.actions["autostart"](on)
                except (NotImplementedError, OSError) as e:
                    self.notes.append(f"autostart: {e}")
                    continue
            self.f.set(fid, on, by="setup")
            self.changed.append(f"{fid}={'on' if on else 'off'}")
        for fid, on in want.items():
            dep = next((d for d in feat.CATALOG[fid].needs if d in feat.CATALOG and not self.f.enabled(d)), None)
            if on and dep:
                self.notes.append(f"{feat.CATALOG[fid].title_en}: {self.t('needs_parent')} "
                                  f"{feat.CATALOG[dep].title_en} ({dep})")

    def step_privacy(self) -> None:
        from . import privacy
        cur = _privacy_on(self.cfg, self.home)
        self.say("\n" + self.t("privacy_note"))
        # A fresh install recommends privacy ON (nothing needs the words yet); a re-run offers the current choice.
        on = self.yesno("privacy_mode", self.t("privacy"), True if self.first else cur)
        if on != cur:
            privacy.set_privacy(SimpleNamespace(home=self.home, cfg=self.cfg, config_path=self.config_path), on)
            self.changed.append(f"privacy={'on' if on else 'off'}")

    def step_budget(self) -> None:
        if self.cfg.ai.connection not in ("combined", "api_key"):
            return
        from . import guides
        from .usage import get_settings
        cur = get_settings(self.home).get("api_monthly_budget_usd")

        def parse(s: str) -> float:
            v = float(s.replace("$", ""))
            if not 0 < v <= 100000:
                raise ValueError(s)
            return v
        if self.scripted:
            v = self.answers.get("monthly_budget_usd", cur)
            if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v <= 100000):
                raise AnswerError("monthly_budget_usd must be a positive number or null")
        else:
            v = self._ask(f"\n{self.t('budget')} [{cur if cur else ''}]: ", parse, cur)
        if v and v != cur:
            guides.set_budget(self.home, v)
            self.changed.append("budget")

    def step_google(self) -> None:
        from .google_auth import GoogleAuth
        on = [fid for fid in GOOGLE_FEATURES if fid in feat.CATALOG and self.f.enabled(fid)]
        if not on or GoogleAuth(self.home).status()["connected"]:
            return
        start = self.actions.get("google_start")
        # Never in a silent/scripted run: it opens a browser and needs the person at the keyboard.
        if not self.scripted and start is not None and self.yesno("connect_google_now", "\n" + self.t("google_now"), False):
            start()
            self.f.invalidate()
        else:
            self.notes.append(self.t("google_later"))

    def step_connect(self) -> None:
        """The wizard ends by offering `jarvis connect` (owner request 20 Oct); never in a silent/scripted run."""
        start = self.actions.get("connect_start")
        if not self.scripted and start is not None and self.yesno("connect_now", "\n" + self.t("connect_now"), True):
            start()
            self.f.invalidate()
        else:
            self.notes.append(self.t("connect_later"))

    def summary(self) -> None:
        self.f.invalidate()
        ident = self._identity()
        n, w, v = ident.names(), ident.wake(), ident.voice()
        self._close()
        self.say("\n== " + self.t("summary") + " ==")
        self.say(f"{self.L('שם', 'Name')}: {n['display_name']} / {n['spoken_name']};  "
                 f"{self.L('מילת הפעלה', 'wake phrase')}: \"{w['phrase']}\";  {self.L('קול', 'voice')}: {v['current']};  "
                 f"{self.L('חיבור', 'connection')}: {self.cfg.ai.connection}")
        for gid, he, en, ids in groups():
            ons = []
            for fid in ids:
                if not self.f.enabled(fid):
                    continue
                c = feat.CATALOG[fid]
                ok = self.f.available(fid)[0]
                ons.append((c.title_he if self.lang == "he" else c.title_en) + ("" if ok else " (!)"))
            self.say(f"  {self.L(he, en)}: {', '.join(ons) or self.t('off')}")
        steps = next_steps(self.f)
        if steps or self.notes:
            self.say("\n" + self.t("next") + ":")
            for line in dict.fromkeys(self.notes):
                self.say(f"  - {line}")
            for s in steps[:12]:
                title = s["title_he"] if self.lang == "he" else s["title_en"]
                step = s["step_he"] if self.lang == "he" else s["step_en"]
                self.say(f"  - {title}: {step}  ({self.L('מדריך', 'guide')}: jarvis dashboard {s['dashboard']})")
            if any(not self.f.available(x)[0] for x in ("music",) if x in feat.CATALOG and self.f.enabled(x)):
                self.say(f"  {self.t('pear')} {PEAR_URL}")
        self.say("\n" + self.t("done") + "\n" + self.t("change"))


# ---------------------------------------------------------------------- helpers
def _has_secret(answers: dict) -> bool:
    text = json.dumps(answers, ensure_ascii=False)
    return bool(re.search(r"sk-[A-Za-z0-9_-]{8,}|\"(api_key|password|token|secret)\"\s*:", text, re.I))


def _cli_features(cfg, home: Path) -> feat.Features:
    from .cli import _cli_features as make
    return make(cfg, home)


def _ask_key(home: Path) -> int:
    from .cli import _ask_key as ask
    return ask(home)


def _prepare(fid: str, f: feat.Features, home: Path) -> tuple[bool, str]:
    """Allowlisted prerequisites (feature_setup.prepare) only where one exists: camera trio (OpenCV, SFace model),
    the wake word (voice extra) and the browser (browser extra: Playwright). Everything else needs no installation."""
    from .feature_setup import CAMERA_FEATURES, prepare
    if fid not in CAMERA_FEATURES and fid not in ("wake_word", "browser"):
        return True, ""
    return prepare(fid, f, home)


def _autostart(on: bool) -> None:
    from .runtime import autostart as au
    ad = au.adapter()
    if on:
        ad.enable()
    else:
        ad.disable()


def _privacy_on(cfg, home: Path) -> bool:
    from .guides import _probe_privacy
    return bool(_probe_privacy(cfg, home)[0])


def _user_md(home: Path) -> Path:
    return home / "workspace" / "USER.md"


def current_address(home: Path) -> str:
    try:
        text = _user_md(home).read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("- " + ADDRESS_MARK):
            return line[len("- " + ADDRESS_MARK):].strip()
    return ""


def set_address(home: Path, value: str) -> None:
    """One managed line in USER.md (replaced on a re-run, removed when empty); the rest of the file is the user's."""
    from .workspace import Workspace
    ws = Workspace(home / "workspace")
    text = ws.read("USER.md")
    lines = [l for l in text.splitlines() if not l.startswith("- " + ADDRESS_MARK)]
    value = " ".join(value.split())
    if value:
        lines.append(f"- {ADDRESS_MARK} {value}")
    ws.write("USER.md", "\n".join(lines).rstrip("\n") + "\n", by="user", note="setup wizard")


# ---------------------------------------------------------------------- add-on bundle check
def _mod(name: str) -> Callable[[], tuple[bool, str]]:
    def check() -> tuple[bool, str]:
        try:
            importlib.import_module(name)
            return True, name
        except Exception as e:
            return False, f"{name}: {type(e).__name__}: {str(e)[:120]}"
    return check


def _data(pkg: str, rel: str) -> Callable[[], tuple[bool, str]]:
    def check() -> tuple[bool, str]:
        try:
            p = importlib.resources.files(pkg).joinpath(rel)
            ok = p.is_file()
        except Exception as e:
            return False, f"{pkg}/{rel}: {type(e).__name__}"
        return ok, f"{pkg}/{rel}" + ("" if ok else " missing")
    return check


def _tz(key: str) -> Callable[[], tuple[bool, str]]:
    def check() -> tuple[bool, str]:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(key)
            return True, f"tz {key}"
        except Exception as e:
            return False, f"tz {key}: {type(e).__name__} (tzdata missing)"
    return check


def _win(check: Callable[[], tuple[bool, str]]) -> Callable[[], tuple[bool, str]]:
    return check if sys.platform == "win32" else (lambda: (True, "n/a off Windows"))


def _fts5() -> Callable[[], tuple[bool, str]]:
    """SW-V4 G: the Python build's SQLite must have FTS5 with the porter/unicode61 tokenizers (recall index)."""
    def check() -> tuple[bool, str]:
        import sqlite3
        try:
            db = sqlite3.connect(":memory:")
            try:
                db.execute("CREATE VIRTUAL TABLE t USING fts5(a, tokenize = 'porter unicode61 remove_diacritics 2')")
            finally:
                db.close()
            return True, f"sqlite {sqlite3.sqlite_version} fts5"
        except sqlite3.Error as e:
            return False, f"SQLite FTS5 missing ({str(e)[:80]})"
    return check


_GOOGLE = [_mod("jarves.google_auth"), _mod("jarves.google_setup"), _mod("jarves.skills.google")]
# feature id -> what must be IN the install for it to work (code, wheels, models, data). External prerequisites
# (Pear Desktop, keys, Google console, a webcam) are probed by the feature itself, not here.
BUNDLE: dict[str, list[Callable[[], tuple[bool, str]]]] = {
    "music": [_mod("jarves.skills.music"), _mod("httpx")],
    "web_search": [_mod("agents")],
    "background_tasks": [_mod("jarves.tasks")],
    "memory_curator": [_mod("jarves.curator")],
    "memory_llm_pass": [_mod("jarves.curator")],
    "diagnosis_llm_pass": [_mod("jarves.diagnose")],
    "self_check": [_mod("jarves.selfcheck"), _data("jarves", "dashboard_static/selfcheck.js"),
                   _data("jarves.guides", "data/self_check.toml")],
    # SW-V4 O; diag/log_codes.json is built at packaging time (root-cause hints) and optional in a dev checkout
    "self_diagnostics_2": [_mod("jarves.diag.service"), _mod("jarves.diag.monitor"), _mod("jarves.diag.ai_review"),
                           _mod("jarves.diag.canary"), _data("jarves", "dashboard_static/health.js"),
                           _data("jarves.guides", "data/health.toml")],
    "self_tests": [_mod("jarves.selftest"), _mod("jarves.speedtest"), _mod("jarves.selfspeed"), _mod("jarves.procstats"),
                   _data("jarves", "dashboard_static/selfspeed.js"), _data("jarves.guides", "data/self_tests.toml")],
    "guided_setup": [_mod("jarves.connect"), _mod("jarves.google_setup"), _mod("jarves.telegram_channel"),
                     _data("jarves.guides", "data/guided_setup.toml")],
    "google_calendar": _GOOGLE, "calendar_write": _GOOGLE, "gmail_read": _GOOGLE, "gmail_send": _GOOGLE,
    "google_contacts": [*_GOOGLE, _mod("jarves.skills.contacts")],
    "camera": [_mod("cv2"), _mod("jarves.camera.service"), _data("jarves.camera", "models/face_detection_yunet_2023mar.onnx")],
    # SFace (38.7 MB) is downloaded, SHA256-pinned, on the first switch-on (feature_setup.prepare), not shipped.
    "face_recognition": [_mod("cv2"), _mod("jarves.camera.faces")],
    "camera_when_away": [_mod("cv2"), _mod("jarves.camera.presence")],
    "camera_preview": [_mod("cv2"), _mod("jarves.camera.frames")],
    "wake_word": [_mod("sounddevice"), _mod("pyopen_wakeword"), _data("pyopen_wakeword", "models/hey_jarvis.tflite"),
                  _mod("jarves.voice.wake_train")],
    "greeting": [_mod("jarves.voice.tts")],
    "chimes": [_mod("sounddevice")],
    "voice_change": [_mod("jarves.skills.settings"), _mod("agents.realtime")],
    "client_delegation": [_mod("jarves.voice.delegation_client"), _mod("jarves.budget_cli"), _mod("openai"),
                          _data("jarves", "dashboard_static/delegation.js"),
                          _data("jarves.guides", "data/client_delegation.toml")],
    "dashboard": [_mod("jarves.dashboard"), _data("jarves", "dashboard_static/index.html"),
                  _data("jarves", "dashboard_static/setup.js"), _data("jarves", "dashboard_static/i18n.js"),
                  _data("jarves", "dashboard_static/shell.js"), _data("jarves", "dashboard_static/locales/en.js"),
                  _data("jarves", "dashboard_static/locales/he.js")],
    "dashboard_lan": [_mod("jarves.dashboard_auth"), _data("jarves", "dashboard_static/pair.html")],
    "mobile_access": [_mod("jarves.mobile"), _mod("jarves.qrmini"), _data("jarves", "dashboard_static/mobile/index.html")],
    "context_trace": [_mod("jarves.context_trace"), _data("jarves", "dashboard_static/context.js")],
    "agent_orchestrator": [_mod("jarves.orchestrator"), _mod("jarves.orchestrator.service"),
                           _mod("jarves.orchestrator.hook"), _mod("jarves.orchestrator.channel"),
                           _data("jarves", "dashboard_static/agents.js"),
                           _data("jarves.guides", "data/agent_orchestrator.toml")],
    "conversation_recall": [_mod("jarves.recall"), _mod("jarves.recall_text"), _fts5(),
                            _data("jarves", "dashboard_static/recall.js"), _data("jarves.guides", "data/recall.toml")],
    "workspace_agent_edit": [_mod("jarves.workspace"), _data("jarves", "setup_guide.md")],
    "assistant_identity": [_mod("jarves.identity")],
    "appearance": [_mod("jarves.appearance")],
    "skill_creation": [_mod("jarves.skills.generated")],
    "proactive_help": [_mod("jarves.guides"), _data("jarves.guides", "data/help.toml")],
    "usage_monitor": [_mod("jarves.usage")],
    "usage_admin_key": [_mod("jarves.usage")],
    "scheduler": [_mod("jarves.scheduler"), _tz("Asia/Jerusalem")],
    "reports": [_mod("jarves.scheduler")],
    "timers": [_mod("jarves.timers"), _tz("Asia/Jerusalem")],
    "lists": [_mod("jarves.lists")],
    "goals": [_mod("jarves.goals")],
    "adaptive_memory": [_mod("jarves.preferences")],
    "notes": [_mod("jarves.notes")],
    "files": [_mod("jarves.sandbox")],
    "proactive_alerts": [_mod("jarves.alerts")],
    "autonomy": [_mod("jarves.autonomy")],
    "backup": [_mod("jarves.backup")],
    "update_check": [_mod("jarves.updates"), _mod("jarves.migrations"), _data("jarves", "dashboard_static/updates.js"),
                     _mod("cryptography.hazmat.primitives.asymmetric.ed25519")],   # the signed-manifest check needs it
    "background_core": [_mod("jarves.runtime.service")],
    "display_control": [_mod("jarves.runtime.viewer")],
    "tray_icon": [_win(_mod("pystray")), _win(_mod("PIL"))],
    "autostart": [_mod("jarves.runtime.autostart")],
    # the browser itself is the installed Edge/Chrome (Windows) or Chromium (Pi), probed by the feature
    "browser": [_mod("playwright.async_api"), _mod("jarves.browser"), _mod("jarves.skills.browser")],
    "telegram": [_mod("jarves.telegram_channel"), _mod("httpx"), _data("jarves.guides", "data/telegram.toml")],
    "instagram": [_mod("jarves.instagram_channel"), _mod("httpx"), _data("jarves.guides", "data/instagram.toml")],
    "whatsapp_handoff": [_mod("jarves.whatsapp"), _data("jarves.guides", "data/whatsapp.toml")],
    # SW-V4 F: the Core side; the bridge binary itself (installer payload bridge/, SHA256-checked by payload_check)
    # is an external process probed by the feature and started by `jarvis whatsapp start`
    "whatsapp_bridge": [_mod("jarves.whatsapp_bridge"), _mod("jarves.whatsapp_admin"), _mod("jarves.whatsapp_policy"),
                        _mod("jarves.whatsapp_store"), _mod("cryptography.hazmat.primitives.ciphers.aead"),
                        _mod("websockets.asyncio.client"), _data("jarves", "dashboard_static/whatsapp.js"),
                        _data("jarves.guides", "data/whatsapp_bridge.toml")],
    "whatsapp_send": [_mod("jarves.whatsapp_bridge"), _mod("jarves.approvals"), _mod("jarves.approval_popup")],
    "assisted_dialing": [_mod("jarves.dialing"), _win(_mod("winreg")), _data("jarves", "dashboard_static/dialing.js"),
                         _data("jarves.guides", "data/assisted_dialing.toml")],
    "morning_briefing": [_mod("jarves.briefing"), _mod("httpx"), _data("jarves.guides", "data/morning_briefing.toml")],
    "home_assistant": [_mod("jarves.homeassistant"), _mod("httpx"), _data("jarves.guides", "data/home_assistant.toml")],
    "profiles": [_mod("jarves.profiles"), _data("jarves", "dashboard_static/profiles.js"),
                 _data("jarves.guides", "data/profiles.toml")],
    "helper": [_mod("jarves.skills.helper"), _mod("jarves.approvals"), _mod("jarves.approval_popup"), _win(_mod("tkinter")),
               _data("jarves.guides", "data/helper.toml")],
    "helper_readonly_auto": [_mod("jarves.skills.helper")],
}
# Not switches, but shipped parts the add-ons rely on.
EXTRA_BUNDLE: dict[str, list[Callable[[], tuple[bool, str]]]] = {
    "live_voice": [_mod("agents.realtime"), _mod("websockets")],
    "chatgpt_sign_in": [_mod("jarves.chatgpt_auth"), _mod("jwt")],
    "privacy": [_mod("jarves.privacy")],
    "approvals": [_mod("jarves.approvals"), _mod("jarves.approval_popup"), _win(_mod("tkinter")),
                  _data("jarves", "dashboard_static/approvals.js")],
    "windows_tts": [_win(_mod("winrt.windows.media.speechsynthesis"))],
    "offline_stt": [_mod("faster_whisper")],          # its Whisper model downloads on first use (not shipped)
    "guides": [_data("jarves.guides", "data/music.toml")],
    "setup_wizard": [_mod("jarves.setup_wizard")],
}


def check_addons(bundle: dict | None = None) -> dict:
    """Is each add-on's code / model / data in THIS install? {"ok", "rows": [...], "unregistered": [...]}."""
    load_failed = load_addons()
    rows = []
    for fid, checks in {**(bundle if bundle is not None else BUNDLE), **EXTRA_BUNDLE}.items():
        results = [c() for c in checks]
        bad = [d for ok, d in results if not ok]
        rows.append({"id": fid, "registered": fid in feat.CATALOG or fid in EXTRA_BUNDLE, "ok": not bad,
                     "missing": bad, "checked": [d for _, d in results]})
    known = set(bundle if bundle is not None else BUNDLE)
    unlisted = [fid for fid in feat.CATALOG if fid not in known]
    return {"ok": all(r["ok"] for r in rows) and not load_failed, "rows": rows, "not_declared": unlisted,
            "load_failed": load_failed}


def print_check(result: dict, write: Callable[[str], None] = print) -> None:
    for r in result["rows"]:
        write(f"[{' OK ' if r['ok'] else 'FAIL'}] {r['id']:<22} " + ("; ".join(r["missing"]) if r["missing"]
                                                                       else ", ".join(r["checked"])))
    for name in result["load_failed"]:
        write(f"[FAIL] add-on module did not load: {name}")
    if result["not_declared"]:
        write("[INFO] registered without a bundle declaration: " + ", ".join(result["not_declared"]))
    write("ADDON CHECK: " + ("PASS" if result["ok"] else "FAIL"))


# ---------------------------------------------------------------------- dashboard
def dashboard_routes(register) -> None:
    def get(ctx, _m, _b):
        return 200, view(ctx.cfg, ctx.home, feat._ctx_features(ctx))
    register("GET", r"/api/setup", get)
