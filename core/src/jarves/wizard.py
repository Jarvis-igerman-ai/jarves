"""The ONE setup-and-tour wizard (owner issues #58, #60, #61): dashboard page "Setup and tour" (#/setup), `jarvis tour`.

Topic by topic: SET UP (Set up / Skip / Later, reuse a working old connection, secrets only into hidden-input fields ->
secrets.env) -> TEST ("Do it for me" runs a safe local check, then the OWNER says Works / Does not work / Skip; a failure
writes one bug-report entry with content-free diagnostics) -> LEARN (30-second card built from the guides and the CLI
parity table, so it never drifts from them).

First run = BASELINE over every topic; later versions = DIFF (topics whose features are new/changed in the CHANGELOG
or never verified) + SMOKE (the five human rows) while the rest stays collapsed. The owner's "Works" writes, per feature,
"verified in version X by owner on <date>" (wizard.json) and `signoff()` generates the sheet
docs/reviews/MANUAL_TEST_<version>.md that `jarvis release signoff --finish` and tools/release_gate.py consume.

Nothing here touches the network or a sensor on its own: tests are the guides' local checks. Texts are Hebrew + English.
"""
from __future__ import annotations

import json
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import __version__, features as feat, guides, logs, release_signoff as so
from .channels_common import read_json, write_private_json

FEATURE = "setup_tour"
STATE_FILE = "wizard.json"
SETUP_CHOICES = ("done", "skipped", "later")
RESULTS = ("works", "fails", "skip")
SMOKE = ("voice", "conversation", "approvals", "music", "dashboard", "update_backup")     # ~5-6 human rows per release
# Tests that only look at local files / switches (no network, no paid call): the automated summary runs only these.
SAFE_TESTS = {"feature", "music", "camera", "backup"}


@dataclass
class Secret:
    field: str
    env: str
    he: str
    en: str


@dataclass
class Topic:
    id: str
    he: str
    en: str
    gives_he: str
    gives_en: str
    guides: list[str]
    features: list[str] = field(default_factory=list)
    connect: str = ""            # an item of the `connect` registry (google, telegram, pear, wake, instagram, home)
    page: str = ""               # dashboard page for the learn card
    cli: str = ""                # CLI command for the learn card
    secrets: list[Secret] = field(default_factory=list)
    high: bool = False           # turning its switches on needs a confirmation in the page (sensitive)
    optional: bool = False       # off by choice is a normal, healthy answer


TOPICS: list[Topic] = [
    Topic("voice", "קול ומילת הפעלה", "Voice and wake word",
          "אומרים Hey Jarvis והוא מקשיב ועונה בקול.", "Say Hey Jarvis and he listens and answers aloud.",
          ["voice_ai", "wake_word", "custom_wake_phrase"], ["wake_word"], "wake", "#/identity", "jarvis wake test"),
    Topic("conversation", "שיחה, זיכרון וחיפוש", "Conversation, memory and search",
          "שיחה בקול או בטקסט, זיכרון של מה שביקשתם לזכור וחיפוש באינטרנט.",
          "Talk by voice or text; he remembers what you ask and can search the web.",
          ["text_chat", "memory", "web_search", "api_bill"], ["text_chat", "web_search"], "", "#/conversations",
          "jarvis chat", [Secret("openai_admin", "OPENAI_ADMIN_KEY", "מפתח אדמין של OpenAI (לא חובה, לעלות אמיתית)",
                                 "OpenAI admin key (optional, for the real bill)")]),
    Topic("music", "מוזיקה (Pear Desktop)", "Music (Pear Desktop)",
          "מנגן מוזיקה בקול דרך Pear Desktop.", "Plays music by voice through Pear Desktop.",
          ["music"], ["music"], "pear", "#/home", "jarvis music status"),
    Topic("google", "גוגל: ג'ימייל, יומן, אנשי קשר", "Google: Gmail, Calendar, Contacts",
          "קורא יומן ומייל ומוצא אנשי קשר (שליחה רק באישור).", "Reads your calendar and mail and finds contacts (sending only with approval).",
          ["google_calendar", "gmail", "google_contacts"], ["google_calendar", "gmail_read", "google_contacts"],
          "google", "#/calendar", "jarvis google start"),
    Topic("productivity", "טיימרים, רשימות והערות", "Timers, lists and notes",
          "טיימרים, רשימת קניות והערות בקול.", "Timers, shopping lists and notes by voice.",
          ["timers", "lists", "notes_files"], ["timers", "lists", "notes"], "", "#/timers", "jarvis timers"),
    Topic("privacy_camera", "פרטיות ומצלמה", "Privacy and camera",
          "מצב פרטיות, חיווי מצלמה ומיקרופון, והכרת פנים (כבויה כברירת מחדל).",
          "Privacy mode, camera and microphone indicators, and face recognition (off by default).",
          ["privacy", "privacy_indicators", "camera", "face_recognition"], ["camera"], "", "#/settings",
          "jarvis features on camera", high=True, optional=True),
    Topic("approvals", "אישורים ועוזר המחשב", "Approvals and the computer helper",
          "כל פעולה רגישה מבקשת אישור בחלון קטן; העוזר מריץ פקודות רק אחרי אישור.",
          "Every sensitive action asks in a small popup; the helper runs commands only after you approve.",
          ["helper", "autonomy"], ["helper"], "", "#/approvals", "jarvis approvals", high=True, optional=True),
    Topic("whatsapp", "וואטסאפ", "WhatsApp",
          "פותח טיוטה בוואטסאפ; גשר קריאה בלבד אופציונלי עם QR.", "Opens a WhatsApp draft; an optional read-only bridge with a QR code.",
          ["whatsapp", "whatsapp_bridge"], ["whatsapp_handoff"], "", "#/whatsapp", "jarvis whatsapp status", optional=True),
    Topic("telegram", "טלגרם", "Telegram",
          "מדברים עם JARVES גם מהטלפון דרך בוט טלגרם משלכם.", "Talk to JARVES from your phone through your own Telegram bot.",
          ["telegram"], ["telegram"], "telegram", "#/connections", "jarvis telegram start",
          [Secret("telegram", "TELEGRAM_BOT_TOKEN", "טוקן הבוט מ-BotFather", "Bot token from BotFather")], optional=True),
    Topic("instagram", "אינסטגרם", "Instagram",
          "קורא ועונה להודעות באינסטגרם באישור שלכם.", "Reads and answers Instagram messages with your approval.",
          ["instagram"], ["instagram"], "instagram", "#/connections", "jarvis instagram status",
          [Secret("instagram", "INSTAGRAM_ACCESS_TOKEN", "טוקן גישה של אינסטגרם", "Instagram access token")], optional=True),
    Topic("home", "Home Assistant", "Home Assistant",
          "שולט בבית החכם שלכם בקול.", "Controls your smart home by voice.",
          ["home_assistant"], ["home_assistant"], "home", "#/home", "jarvis home status",
          [Secret("home", "HOME_ASSISTANT_TOKEN", "טוקן גישה ארוך של Home Assistant", "Home Assistant long-lived access token")],
          optional=True),
    Topic("dashboard", "לוח הבקרה", "Dashboard",
          "כל ההגדרות, השיחות והעלויות בדפדפן, בעברית ובאנגלית.", "All settings, conversations and costs in the browser, English and Hebrew.",
          ["dashboard", "lan_dashboard", "context"], ["dashboard"], "", "#/home", "jarvis dashboard"),
    Topic("phone", "גישה מהטלפון", "Phone access",
          "פותחים את JARVES מהטלפון ברשת הביתית (כבוי כברירת מחדל).", "Open JARVES from your phone on the home network (off by default).",
          ["mobile_access"], ["mobile_access"], "", "#/devices", "jarvis dashboard --mobile", high=True, optional=True),
    Topic("update_backup", "עדכון, גיבוי וחזרה לאחור", "Update, backup and rollback",
          "בודק עדכונים חתומים, מגבה את הנתונים ומאפשר לחזור לגרסה הקודמת.",
          "Checks signed updates, backs up your data and can roll back to the previous version.",
          ["updates", "backup", "beta_channel"], ["update_check", "backup"], "", "#/updates", "jarvis update --rollback"),
    Topic("diagnostics", "אבחון ובדיקות עצמיות", "Diagnostics and self-tests",
          "בודק את עצמו ואומר מה לתקן, בלי לשלוח כלום החוצה.", "Checks itself and tells you what to fix, without sending anything out.",
          ["health", "diagnosis", "self_tests"], ["self_diagnostics_2", "self_tests"], "", "#/health", "jarvis selftest"),
    Topic("cli", "שורת הפקודה", "Command line",
          "כל מה שבלוח הבקרה אפשר גם בטרמינל: jarvis --help.", "Everything in the dashboard also works in a terminal: jarvis --help.",
          ["help", "guided_setup"], ["guided_setup"], "", "#/guides", "jarvis --help"),
]
BY_ID = {t.id: t for t in TOPICS}
SECRET_FIELDS = {s.field: s for t in TOPICS for s in t.secrets}


# ---------------------------------------------------------------------- state
def stable_version(v: str = __version__) -> str:
    m = re.match(r"\d+\.\d+\.\d+", v)
    return m.group(0) if m else v


def read_state(home: Path) -> dict:
    d = read_json(Path(home) / STATE_FILE)
    d.setdefault("topics", {})
    d.setdefault("features", {})
    return d


def _save(home: Path, st: dict) -> None:
    write_private_json(Path(home) / STATE_FILE, st)


def _topic_entry(st: dict, tid: str) -> dict:
    return st["topics"].setdefault(tid, {})


def _topic(tid: str) -> Topic:
    t = BY_ID.get(tid)
    if t is None:
        raise KeyError(tid)
    return t


def _guides_of(t: Topic) -> list[guides.Guide]:
    g = guides.load()
    return [g[i] for i in t.guides if i in g]


def _features_of(t: Topic) -> list[str]:
    return [f for f in t.features if f in feat.CATALOG]


# ---------------------------------------------------------------------- diff / smoke
def changelog_section(version: str, text: str | None = None) -> str:
    """The CHANGELOG lines of one version (first matching '## <version>...' section); '' when not found."""
    if text is None:
        text = ""
        for d in (Path.cwd(), *Path(__file__).resolve().parents):
            if (d / "CHANGELOG.md").is_file():
                text = (d / "CHANGELOG.md").read_text(encoding="utf-8", errors="replace")
                break
    out, on = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if on:
                break
            on = version in line
            continue
        if on:
            out.append(line)
    return "\n".join(out)


def _words(t: Topic) -> list[str]:
    ws = {t.id.replace("_", " "), t.en.lower(), *t.features, *t.guides}
    ws |= {f.replace("_", " ") for f in t.features} | {g.replace("_", " ") for g in t.guides}
    return [w.lower() for w in ws if len(w) > 3]


def changed_topics(version: str, changelog: str | None = None) -> list[str]:
    """Topics the CHANGELOG of this version mentions (by topic name, feature id or guide id)."""
    sec = changelog_section(version, changelog).lower()
    if not sec:
        return []
    return [t.id for t in TOPICS if any(w in sec for w in _words(t))]


def mode(st: dict) -> str:
    return "diff" if st.get("baseline") else "baseline"


def topic_list(st: dict, version: str = __version__, changelog: str | None = None) -> list[str]:
    """Topic ids the owner is asked about now: all in the baseline; in diff mode changed + never verified + smoke."""
    if mode(st) == "baseline":
        return [t.id for t in TOPICS]
    changed = set(changed_topics(stable_version(version), changelog))
    keep = []
    for t in TOPICS:
        fs = _features_of(t)
        unverified = [f for f in fs if f not in st["features"]]
        if t.id in changed or unverified or t.id in SMOKE:
            keep.append(t.id)
    return keep


# ---------------------------------------------------------------------- views
def _connect_states(home: Path, cfg) -> dict:
    from . import connect
    try:
        return {i["id"]: i for i in connect.items_json(home, cfg)["items"]}
    except Exception as e:                                     # a broken optional module must not stop the tour
        logs.warn("wizard", "WIZARD_CONNECT_FAILED", "connection states unavailable", error_type=type(e).__name__)
        return {}


def _cli_for(t: Topic) -> str:
    return t.cli


def card(t: Topic, f: feat.Features) -> dict:
    """The 30-second LEARN card: from the guides (say phrases, expect, guide link) and the topic's page and command."""
    gs = _guides_of(t)
    say: list[dict] = []
    for g in gs:
        for he, en in g.say:
            if len(say) < 3 and he not in [s["he"] for s in say]:
                say.append({"he": he, "en": en})
    first = gs[0] if gs else None
    return {"gives_he": t.gives_he, "gives_en": t.gives_en, "say": say, "page": t.page, "cli": _cli_for(t),
            "guide": first.id if first else "", "guide_title_he": first.title_he if first else "",
            "guide_title_en": first.title_en if first else "",
            "expect_he": first.expect_he if first else "", "expect_en": first.expect_en if first else "",
            "try_it": (say[0] if say else None)}


def topic_view(t: Topic, st: dict, f: feat.Features, conns: dict) -> dict:
    e = st["topics"].get(t.id, {})
    c = conns.get(t.connect) if t.connect else None
    fs = _features_of(t)
    states = [{"id": x, "enabled": f.enabled(x), "available": f.available(x)[0], "active": f.active(x),
               "risk": feat.CATALOG[x].risk, "title_he": feat.CATALOG[x].title_he, "title_en": feat.CATALOG[x].title_en,
               "verified": st["features"].get(x)} for x in fs]
    connected = bool(c and c["state"] == "connected")
    on = bool(fs) and all(s["enabled"] and s["available"] for s in states)
    return {"id": t.id, "he": t.he, "en": t.en, "gives_he": t.gives_he, "gives_en": t.gives_en,
            "optional": t.optional, "high": t.high, "connect": c, "connected": connected,
            "reusable": connected and e.get("setup") is None,       # a working old connection is found: one tap to keep it
            "features": states, "all_on": on,
            "secrets": [{"field": s.field, "he": s.he, "en": s.en} for s in t.secrets],
            "setup": e.get("setup"), "test": e.get("test"), "note": e.get("note", ""), "learned": bool(e.get("learned")),
            "verified_in": e.get("verified_in"), "verified_on": e.get("verified_on"),
            "status": status_of(e)}


def status_of(e: dict) -> str:
    if e.get("setup") == "skipped":
        return "skipped"
    if e.get("setup") == "later":
        return "later"
    if e.get("test") in RESULTS and e.get("setup") == "done":
        return "done"
    if e.get("setup") == "done":
        return "setup"
    return "todo"


def overview(home: Path, cfg, f: feat.Features) -> dict:
    st = read_state(home)
    conns = _connect_states(home, cfg)
    ids = topic_list(st)
    views = [topic_view(_topic(i), st, f, conns) for i in ids]
    everything = [t.id for t in TOPICS if t.id not in ids]
    finished = sum(1 for v in views if v["status"] in ("done", "skipped"))
    nxt = next((v["id"] for v in views if v["status"] in ("todo", "setup", "later")), "")
    return {"version": __version__, "mode": mode(st), "topics": views, "total": len(views), "finished": finished,
            "percent": int(100 * finished / len(views)) if views else 100, "next": nxt,
            "collapsed": [{"id": i, "he": BY_ID[i].he, "en": BY_ID[i].en} for i in everything],
            "automated": st.get("automated"), "baseline": st.get("baseline"),
            "verified": st["features"], "signoff": signoff_state(home)}


# ---------------------------------------------------------------------- actions
def set_choice(home: Path, tid: str, choice: str) -> dict:
    if choice not in SETUP_CHOICES:
        raise ValueError("choice must be done, skipped or later")
    t = _topic(tid)
    st = read_state(home)
    e = _topic_entry(st, t.id)
    e["setup"], e["ts"] = choice, time.time()
    if choice == "skipped":
        e.pop("test", None)
    _save(home, st)
    logs.info("wizard", "WIZARD_SETUP_CHOICE", "tour topic choice", item=t.id, decision=choice)
    return e


def switch_on(home: Path, f: feat.Features, tid: str, confirmed: bool = False) -> dict:
    """SET UP for a topic with switches: turns its features on (through the same Features.set as everywhere)."""
    t = _topic(tid)
    if t.high and not confirmed:
        raise PermissionError("this topic is sensitive; confirm in the page first")
    out = []
    for fid in _features_of(t):
        out.append(f.set(fid, True, by="wizard"))
    return {"features": out}


def save_secret(home: Path, field_name: str, value: str) -> dict:
    """The only place a secret enters the wizard: straight to secrets.env, never to wizard.json or the logs."""
    s = SECRET_FIELDS.get(field_name)
    if s is None:
        raise KeyError(field_name)
    value = (value or "").strip()
    if not value:
        raise ValueError("empty")
    if s.env == "OPENAI_ADMIN_KEY":
        from . import usage
        usage.save_admin_key(home, value)
    else:
        if s.env == "TELEGRAM_BOT_TOKEN" and not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{30,80}", value):
            raise ValueError("that does not look like a Telegram bot token (digits:letters)")
        from .config import save_secret as put
        put(s.env, value, home)
        logs.info("wizard", "WIZARD_SECRET_SAVED", "tour saved a secret", item=s.field)
    return {"saved": True, "field": s.field}


def run_test(home: Path, cfg, f: feat.Features, tid: str) -> dict:
    """'Do it for me': a safe local check (the guide's own test, plus the connection state); never a paid or sensor call."""
    t = _topic(tid)
    parts, ok = [], True
    conns = _connect_states(home, cfg)
    if t.connect and t.connect in conns:
        c = conns[t.connect]
        good = c["state"] == "connected"
        ok = ok and (good or t.optional)
        parts.append({"name": "connection", "ok": good, "he": c["detail_he"] or c["title_he"], "en": c["detail_en"] or c["title_en"]})
    for g in _testable(t):
        r = guides.run_test(g, f)
        parts.append({"name": g.id, "ok": r["ok"], "he": r["he"], "en": r["en"]})
        ok = ok and (r["ok"] or t.optional and not _features_on(t, f))
    if not parts:
        parts.append({"name": "info", "ok": True, "he": "אין בדיקה אוטומטית: נסו את הדוגמה בכרטיס הלימוד.",
                      "en": "No automatic check: try the example on the learn card."})
    return {"ok": ok, "parts": parts, "ts": time.time()}


def _testable(t: Topic) -> list[guides.Guide]:
    """The topic's guides that have a test AND whose test is a local check (no network, no paid call)."""
    return [g for g in _guides_of(t)[:2] if (g.test or g.features) and (g.test or "feature") in SAFE_TESTS]


def _features_on(t: Topic, f: feat.Features) -> bool:
    return any(f.enabled(x) for x in _features_of(t))


def diagnostics(home: Path, cfg, f: feat.Features, t: Topic) -> dict:
    """Content-free: ids, booleans, codes and versions only. No text the owner wrote, no paths, no account names."""
    conns = _connect_states(home, cfg)
    return {"topic": t.id, "version": __version__, "python": platform.python_version(), "os": sys.platform,
            "connect": (conns.get(t.connect) or {}).get("state") if t.connect else None,
            "features": {x: {"enabled": f.enabled(x), "available": f.available(x)[0], "active": f.active(x)}
                         for x in _features_of(t)},
            "tests": {g.id: bool(guides.run_test(g, f)["ok"]) for g in _testable(t)}}


def bug_report(ctx, t: Topic, f: feat.Features, note: str) -> str:
    """One entry in the Issues list (store.db) with content-free diagnostics; the owner's note is the detail."""
    diag = diagnostics(ctx.home, ctx.cfg, f, t)
    fp = f"setup_tour:{t.id}:{stable_version()}"
    now = time.time()
    st = ctx.store
    with st._lock:
        row = st._db.execute("SELECT id FROM issues WHERE fingerprint = ?", (fp,)).fetchone()
        ev = json.dumps(diag, ensure_ascii=False)
        title = f"Setup tour: {t.en} does not work"
        if row is None:
            st._db.execute("INSERT INTO issues (fingerprint, kind, severity, title, detail, evidence, first_seen, "
                           "last_seen, count, last_session) VALUES (?,?,?,?,?,?,?,?,1,NULL)",
                           (fp, "setup_tour", "medium", title, (note or "")[:500], ev, now, now))
        else:
            st._db.execute("UPDATE issues SET count=count+1, last_seen=?, detail=?, evidence=?, status='open' WHERE id=?",
                           (now, (note or "")[:500], ev, row["id"]))
        st._db.commit()
    logs.warn("wizard", "WIZARD_TEST_FAILED", "owner reported a tour topic as not working", item=t.id)
    return fp


def record_result(ctx, f: feat.Features, tid: str, result: str, note: str = "") -> dict:
    if result not in RESULTS:
        raise ValueError("result must be works, fails or skip")
    t = _topic(tid)
    home = ctx.home
    st = read_state(home)
    e = _topic_entry(st, t.id)
    e["test"], e["note"], e["ts"] = result, (note or "")[:500], time.time()
    out: dict = {"result": result}
    if result == "works":
        today = date.today().isoformat()
        e["verified_in"], e["verified_on"] = __version__, today
        for fid in _features_of(t):
            st["features"][fid] = {"version": __version__, "date": today, "by": "owner", "topic": t.id}
    elif result == "fails":
        out["bug"] = bug_report(ctx, t, f, note)
        e["bug"] = out["bug"]
    _save(home, st)
    return out


def mark_learned(home: Path, tid: str) -> None:
    st = read_state(home)
    _topic_entry(st, _topic(tid).id)["learned"] = True
    _save(home, st)


def run_automated(home: Path, cfg, f: feat.Features) -> dict:
    """The automated rows as ONE summary: the safe local checks of every topic. The owner only judges the human rows."""
    passed, failed, skipped = 0, [], 0
    for t in TOPICS:
        if not _features_on(t, f) and t.optional:
            skipped += 1
            continue
        r = run_test(home, cfg, f, t.id)
        if r["ok"]:
            passed += 1
        else:
            failed.append(t.id)
    st = read_state(home)
    st["automated"] = {"passed": passed, "failed": failed, "off_by_choice": skipped, "ts": time.time(), "version": __version__}
    _save(home, st)
    return st["automated"]


def finish_baseline(home: Path) -> None:
    st = read_state(home)
    st["baseline"] = {"version": __version__, "date": date.today().isoformat()}
    _save(home, st)


def reset(home: Path) -> None:
    st = read_state(home)
    st["topics"] = {}
    _save(home, st)


# ---------------------------------------------------------------------- sign-off file
def build_sheet(home: Path, existing: so.Sheet | None = None, version: str | None = None) -> so.Sheet:
    """Rows W-<topic> from the wizard results (pass / fail / accepted-blocked for 'skipped by owner') + one W-AUTO row.
    Rows of an existing sheet that are not W-* (e.g. the standing owner rows) are kept untouched."""
    st = read_state(home)
    ver = so.normalise_version(version or stable_version())
    sheet = existing or so.Sheet(version=ver)
    keep = [r for r in sheet.rows if not r.id.startswith("W-")]
    rows: list[so.Row] = []
    auto = st.get("automated") or {}
    ar = so.Row("W-AUTO", "Automated", "Run the automated checks in Setup and tour (one summary)",
                "Every automated check passes")
    if auto and not auto.get("failed"):
        ar.result, ar.note = "pass", f"{auto.get('passed', 0)} passed"
    elif auto:
        ar.result, ar.note = "fail", "failed: " + ", ".join(auto["failed"])
    rows.append(ar)
    shown = set(topic_list(st))
    for t in TOPICS:
        if t.id not in shown:
            continue
        e = st["topics"].get(t.id, {})
        g = _guides_of(t)
        r = so.Row(f"W-{t.id}", t.en, f"Setup and tour: {t.en}, run the test and judge it",
                   (g[0].expect_en if g and g[0].expect_en else t.gives_en))
        if e.get("setup") == "skipped" or e.get("test") == "skip":
            r.result, r.note = "accepted-blocked", "skipped by the owner in Setup and tour"
        elif e.get("test") == "works":
            r.result, r.note = "pass", f"verified by owner on {e.get('verified_on', '')}"
        elif e.get("test") == "fails":
            r.result, r.note = "fail", e.get("note") or "owner reported: does not work"
        rows.append(r)
    sheet.rows = keep + rows
    sheet.signed = False
    return sheet


def signoff_state(home: Path) -> dict:
    return {"version": stable_version(), "file": f"docs/reviews/MANUAL_TEST_{stable_version()}.md"}


def write_signoff(ctx, version: str | None = None, finish: bool = False) -> dict:
    from . import release_cmd
    ver = so.normalise_version(version or stable_version())
    root = release_cmd.repo_root(ctx.home, None)
    try:
        existing = so.load(root, ver)
    except so.SheetError:
        existing = None
    sheet = build_sheet(ctx.home, existing, ver)
    if finish:
        release_cmd._finish(sheet, ctx.home, None, None)
    path = so.save(root, sheet)
    logs.info("wizard", "WIZARD_SIGNOFF", "sign-off sheet written from the tour", version=ver, done=sheet.signed)
    return {**release_cmd.summary(sheet), "file": str(path)}


# ---------------------------------------------------------------------- dashboard routes
def _fx(ctx) -> feat.Features:
    return feat._ctx_features(ctx)


def dashboard_routes(register) -> None:
    def get(ctx, _m, _b):
        return 200, overview(ctx.home, ctx.cfg, _fx(ctx))

    def get_topic(ctx, m, _b):
        if m.group(1) not in BY_ID:
            return 404, {"error": "no such topic"}
        t, f = BY_ID[m.group(1)], _fx(ctx)
        st = read_state(ctx.home)
        return 200, {**topic_view(t, st, f, _connect_states(ctx.home, ctx.cfg)), "card": card(t, f)}

    def post_choice(ctx, m, body):
        try:
            return 200, set_choice(ctx.home, m.group(1), str((body or {}).get("choice") or ""))
        except KeyError:
            return 404, {"error": "no such topic"}
        except ValueError as e:
            return 400, {"error": str(e)}

    def post_switch(ctx, m, body):
        try:
            return 200, switch_on(ctx.home, _fx(ctx), m.group(1), bool((body or {}).get("confirmed")))
        except KeyError:
            return 404, {"error": "no such topic"}
        except PermissionError as e:
            return 403, {"error": str(e)}

    def post_secret(ctx, m, body):
        try:
            return 200, save_secret(ctx.home, m.group(1), str((body or {}).get("value") or ""))
        except KeyError:
            return 404, {"error": "no such field"}
        except ValueError as e:
            return 400, {"error": str(e)}

    def post_test(ctx, m, _b):
        if m.group(1) not in BY_ID:
            return 404, {"error": "no such topic"}
        return 200, run_test(ctx.home, ctx.cfg, _fx(ctx), m.group(1))

    def post_result(ctx, m, body):
        b = body or {}
        try:
            return 200, record_result(ctx, _fx(ctx), m.group(1), str(b.get("result") or ""), str(b.get("note") or ""))
        except KeyError:
            return 404, {"error": "no such topic"}
        except ValueError as e:
            return 400, {"error": str(e)}

    def post_learned(ctx, m, _b):
        try:
            mark_learned(ctx.home, m.group(1))
        except KeyError:
            return 404, {"error": "no such topic"}
        return 200, {"ok": True}

    def post_auto(ctx, _m, _b):
        return 200, run_automated(ctx.home, ctx.cfg, _fx(ctx))

    def post_signoff(ctx, _m, body):
        b = body or {}
        try:
            return 200, write_signoff(ctx, b.get("version"), bool(b.get("finish")))
        except so.SheetError as e:
            return 409, {"error": str(e)}

    def post_ctl(ctx, _m, body):
        op = (body or {}).get("op")
        if op == "finish_baseline":
            finish_baseline(ctx.home)
        elif op == "reset":
            reset(ctx.home)
        else:
            return 400, {"error": "op must be finish_baseline or reset"}
        return 200, {"ok": True}

    register("GET", r"/api/wizard", get)
    register("GET", r"/api/wizard/topic/([a-z_]+)", get_topic)
    register("POST", r"/api/wizard/topic/([a-z_]+)/choice", post_choice)
    register("POST", r"/api/wizard/topic/([a-z_]+)/switch", post_switch)
    register("POST", r"/api/wizard/secret/([a-z_]+)", post_secret)
    register("POST", r"/api/wizard/topic/([a-z_]+)/test", post_test)
    register("POST", r"/api/wizard/topic/([a-z_]+)/result", post_result)
    register("POST", r"/api/wizard/topic/([a-z_]+)/learned", post_learned)
    register("POST", r"/api/wizard/automated", post_auto)
    register("POST", r"/api/wizard/signoff", post_signoff)
    register("POST", r"/api/wizard/control", post_ctl)


# ---------------------------------------------------------------------- CLI: jarvis tour
def add_cli(sub) -> None:
    p = sub.add_parser("tour", help="Setup and tour: set up, test and learn every topic (opens the dashboard page "
                                    "#/setup); --status shows your progress, --signoff writes the sign-off sheet")
    p.add_argument("--status", action="store_true", help="print the progress per topic; change nothing")
    p.add_argument("--json", action="store_true", help="with --status: machine-readable output")
    p.add_argument("--signoff", action="store_true", help="write docs/reviews/MANUAL_TEST_<version>.md from your results")
    p.add_argument("--no-open", action="store_true", help="start the dashboard but do not open the browser")


def cmd_tour(a, cfg, home) -> int:
    from . import clilang
    from .cli import _cli_features
    f = _cli_features(cfg, home)
    if getattr(a, "signoff", False):
        from types import SimpleNamespace
        try:
            r = write_signoff(SimpleNamespace(home=home), None, False)
        except so.SheetError as e:
            print(f"TOUR_SIGNOFF: {e}")
            return 1
        print(clilang.fmt(f"נכתב: {r['file']} ({r['counts']['pass']} עברו, {len(r['open'])} פתוחות).",
                          f"Written: {r['file']} ({r['counts']['pass']} passed, {len(r['open'])} open)."))
        return 0 if not r["open"] else 1
    if getattr(a, "status", False):
        o = overview(home, cfg, f)
        if getattr(a, "json", False):
            print(json.dumps(o, ensure_ascii=False, indent=1, default=str))
            return 0
        print(clilang.fmt(f"סיור והגדרה ({o['mode']}): {o['finished']}/{o['total']} ({o['percent']}%)",
                          f"Setup and tour ({o['mode']}): {o['finished']}/{o['total']} ({o['percent']}%)"))
        for v in o["topics"]:
            print(f"  [{v['status']:^7}] {v['en']}")
        return 0
    from types import SimpleNamespace
    from .cli import cmd_dashboard
    return cmd_dashboard(SimpleNamespace(port=0, no_open=getattr(a, "no_open", False), lan=False, mobile=False,
                                         tls_selfsigned=False, page="#/setup"), cfg, home)


def _register_feature() -> None:
    if FEATURE in feat.CATALOG:
        return
    feat.register(FEATURE, "הגדרה וסיור", "Setup and tour", True, risk="low",
                  about="one wizard in the dashboard (#/setup) that sets up, tests and teaches every topic; your 'works' "
                        "answers become the per-feature 'verified by owner' record and the release sign-off sheet",
                  targets={"windows": {"status": "implemented_unverified",
                                       "evidence": "core/tests/test_wizard.py (state, diff, secrets, bug report, sign-off sheet)"},
                           "linux_arm64_pi": {"status": "planned", "blocker": "pure Python; not run on a Pi yet",
                                              "roadmap_issue": "https://github.com/igalgerman/jarves/issues/1"}})


_register_feature()
