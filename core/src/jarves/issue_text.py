"""Readable texts and classes for the issues (issue #17): a title, what it means and the next step, in English and Hebrew.

The registry and the diagnose output keep internal keys (`FINDING_version:4.0.1.278`, `FINDING_log:WARNING:LINK_FACE_DISCONNECTED`).
Those are never shown: `describe(record)` turns every record into
    title / title_he       one readable line
    meaning / meaning_he   what it means (one or two sentences)
    next_step / next_step_he   what to do
    kind                   "bug" (something is wrong) or "info" (an expected state of how JARVES is run on this PC)
    hint / hint_he         for "info": why this is not a bug.
Environment states that are normal for the way JARVES is run (no Godot face in a headless run, no camera, Pear Desktop
not running, Google never connected) are "info": they stay visible, can be ignored on this PC with one click, and are
NOT counted as open bugs. Pure functions: no files, no network, content-free texts only.
"""
from __future__ import annotations

import re

FSI, PDI = "⁨", "⁩"
_HEB_RUN = re.compile(r"[֐-׿][֐-׿\s'\"׳״\-]*[֐-׿]|[֐-׿]")
_HEB = re.compile(r"[֐-׿]")
_LATIN = re.compile(r"[A-Za-z]")
_QUOTED_HEB = re.compile(r"\"([^\"]*[֐-׿][^\"]*)\"")
_RAW = re.compile(r"^(?:[A-Z][A-Z0-9]*_[A-Za-z0-9_.:+\-]*|[a-z][a-z0-9_]*:[A-Za-z0-9_.:+\-]+)$")

# ---------------------------------------------------------------------- environment states (info, not bugs)
# (key, regex over "code + finding fingerprint", title, title_he, meaning, meaning_he, hint, hint_he)
ENV: tuple[tuple, ...] = (
    ("face_link", r"LINK_FACE_DISCONNECTED|face_link|health:link\b|FINDING_health:link\b",
     "The Godot face is not connected", "פני ה-Godot לא מחוברים",
     "JARVES runs without the Godot face window (a headless run), so the link to the face has nobody to talk to.",
     "ג'ארביס רץ בלי חלון הפנים של Godot (הפעלה בלי תצוגה), ולכן אין עם מי לדבר בחיבור לפנים.",
     "Expected when you start only the Core. Start the face (godot\\run_face.cmd) if you want it.",
     "צפוי כשמפעילים רק את הליבה. רוצים את הפנים? מפעילים את godot\\run_face.cmd."),
    ("camera", r"CAMERA_(OPEN_FAILED|UNAVAILABLE|READ_FAILED|LOOP_ERROR|NO_LIBRARY)|avail:camera|health:camera",
     "No camera available", "אין מצלמה זמינה",
     "A camera feature is switched on but no webcam could be opened (none connected, in use by another app, or blocked).",
     "תכונת מצלמה דולקת אבל אי אפשר לפתוח מצלמה (אין מצלמה, תפוסה בתוכנה אחרת או חסומה).",
     "Not a bug when this PC has no webcam or you do not use the camera.",
     "אין כאן באג אם במחשב הזה אין מצלמה או שלא משתמשים בה."),
    ("pear", r"MUSIC_(PLAYER_OFFLINE|NOT_PAIRED|PAIR_FAILED|TIMEOUT|PLAYER_ERROR)|avail:pear|health:music|Pear Desktop is not running",
     "Pear Desktop (the music player) is not running", "Pear Desktop (נגן המוזיקה) לא פועל",
     "The music feature is on but Pear Desktop is closed, not installed or not paired.",
     "תכונת המוזיקה דולקת אבל Pear Desktop סגור, לא מותקן או לא מוצמד.",
     "Not a bug when you do not use music on this PC.", "אין כאן באג אם לא משתמשים במוזיקה במחשב הזה."),
    ("voice_limit", r"VOICE_IDLE_DISCONNECT|VOICE_SESSION_MAX",
     "A voice conversation ended by its time limit", "שיחה קולית הסתיימה במגבלת הזמן",
     "A voice conversation was closed on purpose after a quiet period (idle disconnect) or at the session maximum, to stop billing.",
     "שיחה קולית נסגרה בכוונה אחרי זמן שקט (ניתוק בחוסר פעילות) או במקסימום הסשן, כדי לא לחייב.",
     "Information, not a bug. Change the limit in config.toml (voice.idle_disconnect_s) if it is too short.",
     "מידע, לא באג. אפשר לשנות את המגבלה ב-config.toml (voice.idle_disconnect_s) אם היא קצרה מדי."),
    ("google", r"GOOGLE_NOT_CONNECTED|GOOGLE_NO_REFRESH_TOKEN",
     "Google is not connected", "גוגל לא מחובר",
     "Calendar and Gmail are switched on but no Google account was connected on this PC.",
     "היומן וג'ימייל דולקים אבל לא חובר חשבון גוגל במחשב הזה.",
     "Not a bug if you do not use Google here.", "אין כאן באג אם לא משתמשים בגוגל כאן."),
    ("live_extra", r"(?<![A-Za-z0-9])LIVE_EXTRA_REJECTED(?![A-Za-z0-9_])",
     "An optional live-voice extra was declined", "תוספת אופציונלית של הקול החי נדחתה",
     "The live voice service refused an optional extra setting; JARVES switched the extra off and the session stayed healthy.",
     "שירות הקול החי דחה הגדרת תוספת אופציונלית; ג'ארביס כיבה את התוספת והשיחה נשארה תקינה.",
     "Information, not a bug: the code already handles it and the conversation works without the extra.",
     "מידע, לא באג: הקוד כבר מטפל בזה והשיחה עובדת גם בלי התוספת."),
    ("wake_mic", r"(?<![A-Za-z0-9])WAKE_MIC_QUIET(?![A-Za-z0-9_])",
     "The microphone level is quiet", "עוצמת המיקרופון נמוכה",
     "The microphone signal is weak, so the wake word may be missed.",
     "אות המיקרופון חלש, ולכן מילת ההפעלה עלולה לא להישמע.",
     "A hint about the room or the microphone gain, not a JARVES bug.",
     "רמז על החדר או על עוצמת המיקרופון, לא באג בג'ארביס."),
    ("stop_update", r"FINDING_(?:stop_update|system_restart)",
     "JARVES was closed by an update or a restart", "ג'ארביס נסגר בגלל עדכון או הפעלה מחדש",
     "A JARVES process ended without its stop record right before a newer version (or a rebooted PC) started it again.",
     "תהליך של ג'ארביס הסתיים בלי רישום עצירה רגע לפני שגרסה חדשה (או מחשב שהופעל מחדש) הפעילה אותו שוב.",
     "Expected after an update or a restart; a stop without such a reason still counts as a problem.",
     "צפוי אחרי עדכון או הפעלה מחדש; עצירה בלי סיבה כזו עדיין נחשבת לבעיה."),
)
ENV_NEXT = {
    "live_extra": ("Nothing to do.", "אין מה לעשות."),
    "wake_mic": ("Speak closer or raise the microphone level in Windows; `jarvis wake test` shows the level.",
                 "לדבר קרוב יותר או להגביר את המיקרופון ב-Windows; jarvis wake test מציג את הרמה."),
    "stop_update": ("Nothing to do unless it keeps happening without an update.",
                    "אין מה לעשות אלא אם זה חוזר בלי עדכון."),
    "face_link": ("Start the face with godot\\run_face.cmd, or ignore this on this PC.",
                  "להפעיל את הפנים עם godot\\run_face.cmd, או להתעלם מזה במחשב הזה."),
    "camera": ("Connect a webcam (guide: Camera) or switch the camera feature off: `jarvis features off camera`.",
               "לחבר מצלמה (מדריך: מצלמה) או לכבות את התכונה: jarvis features off camera."),
    "pear": ("Start Pear Desktop (guide: Music) or switch music off: `jarvis features off music`.",
             "להפעיל את Pear Desktop (מדריך: מוזיקה) או לכבות את המוזיקה: jarvis features off music."),
    "voice_limit": ("Nothing to do. To keep talking longer, raise voice.idle_disconnect_s in config.toml.",
                    "אין מה לעשות. כדי לדבר יותר זמן, מעלים את voice.idle_disconnect_s ב-config.toml."),
    "google": ("Run `jarvis google start` to connect, or ignore this on this PC.",
               "להריץ jarvis google start כדי להתחבר, או להתעלם מזה במחשב הזה."),
}
_ENV_RE = [(k, re.compile(p)) for k, p, *_ in ENV]
_ENV = {e[0]: e for e in ENV}

AREA_NEXT = {
    "selftest": ("Run `jarvis selftest` to see the failing check.", "להריץ jarvis selftest ולראות איזו בדיקה נכשלת."),
    "speed": ("Run `jarvis speedtest`; repeat it once the PC is idle.", "להריץ jarvis speedtest שוב כשהמחשב פנוי."),
    "health": ("Open the Health page; if it keeps coming back report it to the developer.",
               "לפתוח את עמוד התקינות; אם זה חוזר, לדווח למפתח."),
    "tools": ("Open the System log filtered on the tool; if it keeps failing report it to the developer.",
              "לפתוח את יומן המערכת מסונן לפי הכלי; אם זה ממשיך, לדווח למפתח."),
    "core": ("Report it to the developer: `jarvis issues report ID` shows exactly what would be sent.",
             "לדווח למפתח: jarvis issues report ID מציג בדיוק מה יישלח."),
    "dashboard": ("Reload the dashboard page; if it keeps happening report it.", "לרענן את העמוד; אם זה חוזר, לדווח."),
    "manual": ("Report it to the developer when you are ready.", "לדווח למפתח כשמוכנים."),
    "crash": ("Report it to the developer: `jarvis issues report ID` shows exactly what would be sent.",
              "לדווח למפתח: jarvis issues report ID מציג בדיוק מה יישלח."),
}
AREA_HE = {"selftest": "בדיקה עצמית", "speed": "מהירות", "health": "תקינות", "tools": "כלים", "core": "ליבה",
           "dashboard": "לוח הבקרה", "manual": "ידני", "crash": "קריסה"}

_FP = {  # fingerprint of a Self-Diagnostics finding -> (title, title_he, meaning, meaning_he)
    "latency": ("Replies are slow", "התשובות איטיות", "Most spoken replies start later than the limit.",
                "רוב התשובות המדוברות מתחילות אחרי הגבול."),
    "claims": ("JARVES said an action was done when no tool ran", "ג'ארביס אמר שפעולה בוצעה בלי שכלי רץ",
               "A reply claimed something was done but no tool result backs it.", "תשובה טענה שמשהו בוצע אבל אין לכך תוצאת כלי."),
    "crash": ("JARVES stopped without a clean shutdown", "ג'ארביס נעצר בלי כיבוי מסודר",
              "A process ended without writing its stop record (crash, kill or power cut).",
              "תהליך הסתיים בלי לרשום עצירה מסודרת (קריסה, סגירה בכוח או הפסקת חשמל)."),
    "update": ("A newer JARVES is available", "יש גרסה חדשה של ג'ארביס", "A new release was published.", "יצאה גרסה חדשה."),
    "disk": ("Low disk space", "מעט מקום פנוי בדיסק", "The disk with the data folder is nearly full.", "הדיסק עם תיקיית הנתונים כמעט מלא."),
    "memory": ("Low free memory", "מעט זיכרון פנוי", "The PC has little free memory.", "במחשב מעט זיכרון פנוי."),
    "state_size": ("The data folder is large", "תיקיית הנתונים גדולה", "The JARVES data folder grew large.", "תיקיית הנתונים של ג'ארביס גדלה."),
    "setup": ("The setup was never finished", "ההגדרה הראשונית לא הושלמה", "First-run setup is incomplete.", "ההגדרה הראשונית לא הושלמה."),
    "budget:limit": ("The daily AI limit was reached", "הגעתי למגבלת ה-AI היומית", "AI answers stopped until the next day.",
                     "תשובות ה-AI נעצרו עד למחרת."),
    "budget:burn": ("Daily AI cost is above the alert", "עלות ה-AI היומית מעל ההתראה", "The average daily cost is above your alert level.",
                    "העלות היומית הממוצעת מעל סף ההתראה."),
    "wake:false": ("False wake-ups", "התעוררויות שווא", "He woke up several times without anyone speaking.",
                   "הוא התעורר כמה פעמים בלי שמישהו דיבר."),
    "wake:near": ("Wake-word near misses", "כמעט-התעוררויות", "The wake word was almost heard but not accepted.",
                  "מילת ההפעלה כמעט זוהתה אבל לא התקבלה."),
    "anomaly:wake_false": ("Too many false wake-ups per day", "יותר מדי התעוררויות שווא ביום",
                           "He woke without speech more often than the daily target.", "הוא התעורר בלי דיבור יותר מהיעד היומי."),
}


def words(s: str) -> str:
    return re.sub(r"[_:.\-]+", " ", str(s or "")).strip().lower()


def fingerprint_of(code: str) -> str:
    """The Self-Diagnostics fingerprint inside a registry code (`FINDING_x` -> `x`); the code itself otherwise."""
    return code[len("FINDING_"):] if str(code).startswith("FINDING_") else str(code)


def embedded_codes(r: dict) -> set[str]:
    """Log / error codes a record is about (CHAT_TURN_FAILED in FINDING_anomaly:newcode:CHAT_TURN_FAILED), for the
    Fixes-line match and the environment rules."""
    out = {str(r.get("code") or "")}
    fp = fingerprint_of(str(r.get("code") or ""))
    out.add(fp)
    out.update(p for p in re.split(r"[:]", fp) if re.fullmatch(r"[A-Z][A-Z0-9]*_[A-Za-z0-9_.+\-]+", p))
    return {x for x in out if x}


def env_key(r: dict) -> str | None:
    hay = " ".join([str(r.get("code") or ""), str((r.get("evidence") or {}).get("finding") or "")])
    for k, rx in _ENV_RE:
        if rx.search(hay):
            return k
    return None


SEVERITY_GUARDED = frozenset({"live_extra", "wake_mic"})   # warning-level hints only
SPEED_RUNS_TO_FLAG = 3        # a speed item is information until this many runs exceeded the budget


def speed_pending(r: dict) -> bool:
    """A SPEED_* record seen in fewer than SPEED_RUNS_TO_FLAG runs (one record() call per run that exceeded its budget).
    Decided by the code only, never by the area name: other speed-area codes keep their own severity."""
    n = r.get("count", 1)
    n = n if isinstance(n, int) and not isinstance(n, bool) else 1
    return str(r.get("code") or "").startswith("SPEED_") and n < SPEED_RUNS_TO_FLAG


def classify(r: dict) -> str:
    """'info' for an expected environment state, an INFO_ record or a speed item below 3 runs, else 'bug'.
    Only the code decides (never the area), so a real failure in the same area still counts."""
    code = str(r.get("code") or "")
    if code.startswith("INFO_") or fingerprint_of(code).startswith("version:") or speed_pending(r):
        return "info"
    ek = env_key(r)
    if ek in SEVERITY_GUARDED and (r.get("severity") == "P1" or "log:ERROR:" in code):
        return "bug"          # a P1 or an ERROR-level record is a real failure, whatever its code (#433)
    return "info" if ek else "bug"


def looks_raw(title: str, code: str) -> bool:
    t = str(title or "").strip()
    return not t or t == code or bool(_RAW.match(t)) or t.startswith("FINDING_")


def _humanize(r: dict) -> tuple[str, str, str, str]:
    """(title, title_he, meaning, meaning_he) for a record whose stored title is a raw key."""
    code = str(r.get("code") or "")
    fp = fingerprint_of(code)
    area = str(r.get("area") or "core")
    ah = AREA_HE.get(area, area)
    if fp in _FP:
        return _FP[fp]
    if fp.startswith("version:") or code == "INFO_NEW_VERSION":
        return ("A new version was seen", "זוהתה גרסה חדשה", "JARVES compared the first hours of a new version with the previous one. Informational.",
                "ג'ארביס השווה את השעות הראשונות של גרסה חדשה לקודמת. מידע בלבד.")
    m = re.match(r"^anomaly:newcode:(.+)$", fp)
    if m:
        c = m.group(1)
        return (f"New log code {c}", f"קוד יומן חדש {c}", f"The code {c} appeared recently and never in the 14 days before.",
                f"הקוד {c} הופיע לאחרונה ולא הופיע ב-14 הימים שקדמו.")
    m = re.match(r"^anomaly:step:(.+)$", fp)
    if m:
        c = m.group(1)
        return (f"{c} became more frequent", f"{c} הפך תכוף יותר", f"The code {c} is logged much more often than before.",
                f"הקוד {c} נרשם הרבה יותר מבעבר.")
    m = re.match(r"^(?:anomaly:metric|trend):(.+)$", fp)
    if m:
        n = words(m.group(1))
        return (f"{n} is unusually high or rising", f"{n} גבוה או עולה בצורה חריגה", f"The measurement {n} left its normal range.",
                f"המדידה {n} יצאה מהטווח הרגיל.")
    m = re.match(r"^log:(?:ERROR|WARNING):(.+)$", fp)
    if m:
        c = m.group(1)
        return (f"{c} was logged repeatedly", f"{c} נרשם שוב ושוב", f"The code {c} appears many times in the logs.",
                f"הקוד {c} מופיע פעמים רבות ביומנים.")
    m = re.match(r"^health:(.+)$", fp)
    if m:
        n = words(m.group(1))
        return (f"{n} is not working", f"{n} לא עובד", f"The Core reports the component {n} as unavailable or degraded.",
                f"הליבה מדווחת ש-{n} לא זמין או פועל חלקית.")
    m = re.match(r"^avail:(.+)$", fp)
    if m:
        n = words(m.group(1))
        return (f"{n} is not available", f"{n} לא זמין", f"The feature {n} is switched on but cannot be used now.",
                f"התכונה {n} דולקת אבל אי אפשר להשתמש בה כרגע.")
    m = re.match(r"^(?:SELFTEST|SPEED|TOOL|CRASH|ERROR|JS)_(.+)$", code)
    if m:
        n = words(m.group(1))
        return (f"{n} needs attention", f"{n} דורש תשומת לב", f"A {area} check reported {n}.", f"בדיקת {ah} דיווחה על {n}.")
    n = words(fp) or "unknown"
    return (f"{n}", f"בעיה ב{ah}: {n}", f"JARVES noticed something in {area}: {n}.", f"ג'ארביס שם לב למשהו ב{ah}: {n}.")


def describe(r: dict) -> dict:
    """Readable fields for one registry record (see the module doc). Never returns a raw key as the title."""
    kind = classify(r)
    ek = env_key(r)
    code = str(r.get("code") or "")
    he_missing = not _HEB.search(str(r.get("title_he") or ""))
    h_title, h_title_he, h_meaning, h_meaning_he = _humanize(r)
    title, title_he = str(r.get("title") or ""), str(r.get("title_he") or "")
    meaning, meaning_he = str(r.get("meaning") or ""), str(r.get("meaning_he") or "")
    nxt, nxt_he = str(r.get("next_step") or ""), str(r.get("next_step_he") or "")
    hint = hint_he = ""
    if ek:
        _, _, t, th, m, mh, hn, hnh = _ENV[ek]
        title, title_he, meaning, meaning_he, hint, hint_he = t, th, m, mh, hn, hnh
        nxt, nxt_he = ENV_NEXT[ek]
    else:
        if looks_raw(title, code):
            title = h_title
        if looks_raw(title_he, code) or he_missing:
            from . import hebrew
            title_he = (hebrew.he(title) or "") if title != h_title else h_title_he
            if not _HEB.search(title_he):
                title_he = h_title_he
        meaning = meaning or h_meaning
        meaning_he = meaning_he or h_meaning_he
        if not nxt:
            nxt, nxt_he = AREA_NEXT.get(str(r.get("area")), AREA_NEXT["core"])
        elif not nxt_he:
            nxt_he = AREA_NEXT.get(str(r.get("area")), AREA_NEXT["core"])[1]
    if code == "INFO_NEW_VERSION":
        kind = "info"
        nxt, nxt_he = ("Nothing to do unless something feels worse; the Health page has the version report.",
                       "אין מה לעשות אלא אם משהו הרגיש גרוע יותר; דוח הגרסה בעמוד התקינות.")
        hint, hint_he = "A new version is not a problem.", "גרסה חדשה היא לא בעיה."
    if speed_pending(r) and not ek:
        hint = f"Seen in {r.get('count', 1)} run(s). It counts as a problem only after {SPEED_RUNS_TO_FLAG} runs exceed the budget."
        hint_he = f"נראה ב-{r.get('count', 1)} הרצות. נחשב לבעיה רק אחרי {SPEED_RUNS_TO_FLAG} הרצות מעל התקציב."
    ev = r.get("evidence") or {}
    ec = ev.get("count") if isinstance(ev.get("count"), int) and not isinstance(ev.get("count"), bool) else r.get("count", 1)
    closed = ""
    closed_he = ""
    if r.get("status") == "fixed" and r.get("closed_reason") == "quiet":
        lv = r.get("last_version") or "?"
        closed, closed_he = f"Closed: not seen since {lv}", f"נסגרה: לא נראתה מאז גרסה {lv}"
    elif r.get("status") == "fixed" and r.get("closed_reason") == "fixes_line":
        closed, closed_he = f"Closed: fixed in {r.get('fixed_in')}", f"נסגרה: תוקנה בגרסה {r.get('fixed_in')}"
    elif r.get("status") == "fixed":
        closed, closed_he = f"Fixed in {r.get('fixed_in') or '?'}", f"תוקנה בגרסה {r.get('fixed_in') or '?'}"
    if r.get("fixed_note"):
        closed += f" ({r['fixed_note']})"
        closed_he += f" ({r['fixed_note']})"
    return {"kind": kind, "title": title, "title_he": title_he, "meaning": meaning, "meaning_he": meaning_he,
            "next_step": nxt, "next_step_he": nxt_he, "hint": hint, "hint_he": hint_he, "evidence_count": ec,
            "closed": closed, "closed_he": closed_he}


# ---------------------------------------------------------------------- bidi-safe printing
def isolate(text: str) -> str:
    """Hebrew runs wrapped in first-strong isolates, so a Hebrew word inside an English line cannot reorder its
    neighbours (the dashboard and files; a legacy Windows console may not draw the marks: use `split_mixed` there)."""
    if not (_HEB.search(text) and _LATIN.search(text)):
        return text
    return _HEB_RUN.sub(lambda m: FSI + m.group(0) + PDI, text)


def split_mixed(text: str) -> tuple[str, list[str]]:
    """('line with "#1" in place of each quoted Hebrew part', [Hebrew parts]) - print the parts on their own lines."""
    parts: list[str] = []

    def sub(m: re.Match) -> str:
        parts.append(m.group(1))
        return f'"#{len(parts)}"'
    line = _QUOTED_HEB.sub(sub, str(text or ""))
    if _HEB.search(line) and _LATIN.search(line):                     # unquoted Hebrew in an English line
        def sub2(m: re.Match) -> str:
            parts.append(m.group(0))
            return f"#{len(parts)}"
        line = _HEB_RUN.sub(sub2, line)
    return re.sub(r"\s{2,}", " ", line).strip(), parts


def format_mixed(prefix: str, title: str, suffix: str = "") -> list[str]:
    """The printable lines of `prefix title suffix`: one clean line, plus one indented line per Hebrew part."""
    line, parts = split_mixed(title)
    out = [f"{prefix}{line}{suffix}"]
    out += [f"      #{i}: {p}" for i, p in enumerate(parts, 1)]
    return out
