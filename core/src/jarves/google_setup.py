"""`jarvis google start`: the guided, resumable Google connection (SW-V2 WP-K, Issue #17; user: "just type jarvis
google start and it helps me out; everything must be simple").

One command for everything:
- first time: walks the Google Cloud console ONE step at a time (opens the exact page, copies the value to paste,
  says what to click), watches the Downloads folder for the client_secret*.json and takes it (moved into the private
  <state_dir>/google folder, contents never printed), signs in, runs a live read-only self-check (calendar list,
  Gmail profile), switches the features on (google_calendar, gmail_read; gmail_send stays OFF unless --send) and
  ends with a spoken-style summary and example phrases;
- the weekly reconnect (Testing-mode apps lose the refresh token after 7 days): it sees needs_reconnect and only
  signs in again;
- already connected: it only re-checks. Progress is saved (<state_dir>/google/setup.json), so `q` and Ctrl+C are
  safe and the next run continues where it stopped.

Console deep links checked 2026-10-19 against Google's Calendar API Python quickstart
(developers.google.com/workspace/calendar/api/quickstart/python): flows/enableapi?apiid=..., auth/branding,
auth/audience, auth/scopes, auth/clients ("Google Auth platform"). gcloud can only do the first two steps
(`gcloud projects create`, `gcloud services enable`); the consent screen and a Desktop OAuth client have no public
gcloud/API for personal accounts, so those stay manual. gcloud is used only when it is already installed AND the
user says yes.

The dashboard Connections page shows the same checklist (dashboard_routes below). Nothing here prints or logs
tokens, the client secret or the e-mail address.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable

import httpx

from . import logs
from .chatgpt_auth import _read_json, _write_private
from .google_auth import (ALL_SCOPES, CALENDAR_SCOPES, GMAIL_READ, START_COMMAND, GoogleAuth, GoogleAuthError,
                          short_scope)

CONSOLE = "https://console.cloud.google.com"
CAL_LIST = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
GMAIL_PROFILE = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
TESTING_DAYS = 7                     # Testing-mode refresh tokens expire after 7 days (Google OAuth docs)
WATCH_TIMEOUT_S = 900
APP_NAME = "JARVES"
PROJECT_NAME = "JARVES home"
EXAMPLES_HE = ["מה יש לי מחר ביומן?", "מתי הפגישה הבאה שלי?", "יש מיילים חשובים?"]

# One console step = one page. "copy": what goes to the clipboard ({email}, {scopes} filled in).
STEPS: list[dict] = [
    {"id": "project", "url": "/projectcreate", "copy": PROJECT_NAME,
     "title_he": "יוצרים פרויקט בגוגל קלאוד (חינם)", "title_en": "Create a Google Cloud project (free)",
     "clicks_he": ["נכנסים עם חשבון הג'ימייל שלך", "Project name: מדביקים (Ctrl+V)", "לוחצים Create",
                   "מחכים כמה שניות עד שהפרויקט נבחר למעלה"],
     "clicks_en": ["Sign in with your Gmail account", "Project name: paste (Ctrl+V)", "Click Create",
                   "Wait until the new project is selected at the top"]},
    {"id": "calendar_api", "url": "/flows/enableapi?apiid=calendar-json.googleapis.com", "copy": "",
     "title_he": "מפעילים את Google Calendar API", "title_en": "Enable the Google Calendar API",
     "clicks_he": ["בודקים שהפרויקט JARVES home בחור למעלה", "Next ואז Enable"],
     "clicks_en": ["Check that JARVES home is the selected project", "Next, then Enable"]},
    {"id": "gmail_api", "url": "/flows/enableapi?apiid=gmail.googleapis.com", "copy": "", "gmail": True,
     "title_he": "מפעילים את Gmail API", "title_en": "Enable the Gmail API",
     "clicks_he": ["אותו פרויקט", "Next ואז Enable"], "clicks_en": ["Same project", "Next, then Enable"]},
    {"id": "branding", "url": "/auth/branding", "copy": APP_NAME,
     "title_he": "מסך ההסכמה: שם האפליקציה", "title_en": "Consent screen: app name",
     "clicks_he": ["Get started", "App name: מדביקים JARVES", "User support email: בוחרים את המייל שלך", "Next",
                   "Audience: בוחרים External, Next", "Contact information: המייל שלך, Next",
                   "מסמנים הסכמה, Continue, Create"],
     "clicks_en": ["Get started", "App name: paste JARVES", "User support email: pick your address", "Next",
                   "Audience: External, Next", "Contact information: your address, Next",
                   "Tick the agreement, Continue, Create"]},
    {"id": "audience", "url": "/auth/audience", "copy": "{email}",
     "title_he": "מוסיפים את עצמך כמשתמש בדיקה", "title_en": "Add yourself as a test user",
     "clicks_he": ["למטה: Test users, ואז Add users", "מדביקים את המייל שלך", "Save",
                   "לא לוחצים Publish app (נשארים במצב Testing)"],
     "clicks_en": ["Bottom: Test users, then Add users", "Paste your address", "Save",
                   "Do not click Publish app (stay in Testing)"]},
    {"id": "scopes", "url": "/auth/scopes", "copy": "{scopes}",
     "title_he": "ההרשאות ש-JARVES מבקש", "title_en": "The permissions JARVES asks for",
     "clicks_he": ["Add or remove scopes", "למטה בתיבה Manually add scopes: מדביקים", "Add to table, Update",
                   "Save"],
     "clicks_en": ["Add or remove scopes", "Bottom box 'Manually add scopes': paste", "Add to table, Update",
                   "Save"]},
    {"id": "client", "url": "/auth/clients", "copy": APP_NAME,
     "title_he": "יוצרים מפתח למחשב (Desktop app) ומורידים אותו", "title_en": "Create a Desktop client and download it",
     "clicks_he": ["Create client", "Application type: Desktop app", "Name: מדביקים JARVES", "Create",
                   "בחלון שנפתח: Download JSON (אני אמצא את הקובץ בהורדות לבד)"],
     "clicks_en": ["Create client", "Application type: Desktop app", "Name: paste JARVES", "Create",
                   "In the dialog: Download JSON (I pick the file up from Downloads myself)"]},
]
STEP_IDS = [s["id"] for s in STEPS]

# Common errors -> what to do, in plain words (Hebrew first) + the console step that fixes it.
FIXES: dict[str, tuple[str, str, str]] = {
    "GOOGLE_CONSENT_DENIED": ("גוגל לא קיבל אישור. אם לחצת Cancel: פשוט מריצים שוב. אם כתוב 'access blocked' או "
                              "'access_denied': הכתובת שלך לא ברשימת משתמשי הבדיקה, מוסיפים אותה.",
                              "Sign-in was not approved. If you pressed Cancel, just run it again. If Google said "
                              "'access blocked' / access_denied, add your address as a test user.", "audience"),
    "GOOGLE_SIGNIN_TIMEOUT": ("ההתחברות לא הסתיימה. אם גוגל הראה 'Access blocked' או 'has not completed the "
                              "verification process': מוסיפים את המייל שלך כמשתמש בדיקה ומנסים שוב.",
                              "Sign-in did not finish. If Google showed 'Access blocked' / 'has not completed the "
                              "verification process', add your address as a test user and try again.", "audience"),
    "oauth:redirect_uri_mismatch": ("המפתח שהורדת הוא לא מסוג Desktop app. יוצרים מפתח חדש מסוג Desktop app.",
                                    "The downloaded client is not a Desktop app client. Create a Desktop app client.",
                                    "client"),
    "GOOGLE_CLIENT_INVALID": ("הקובץ הוא לא מפתח מסוג Desktop app (אולי Web application). יוצרים Desktop app "
                              "ומורידים שוב.", "That file is not a Desktop app client (maybe Web application). "
                              "Create a Desktop app client and download it again.", "client"),
    "oauth:invalid_client": ("גוגל לא מכיר את המפתח (נמחק או הוחלף). מורידים מפתח חדש.",
                             "Google does not know this client (deleted or replaced). Download a new one.", "client"),
    "oauth:invalid_grant": ("החיבור פג (כל 7 ימים במצב Testing) או בוטל. מתחברים שוב: " + START_COMMAND,
                            "The connection expired (every 7 days in Testing) or was revoked. Connect again: "
                            + START_COMMAND, ""),
    "GOOGLE_AUTH_EXPIRED": ("החיבור פג (כל 7 ימים במצב Testing). מתחברים שוב: " + START_COMMAND,
                            "The connection expired (every 7 days in Testing). Connect again: " + START_COMMAND, ""),
    "oauth:org_internal": ("האפליקציה מוגדרת Internal. ב-Audience בוחרים External.",
                           "The app is set to Internal. In Audience choose External.", "audience"),
    "GOOGLE_NO_REFRESH_TOKEN": ("גוגל לא נתן מפתח קבוע. מוחקים את JARVES ב-myaccount.google.com/connections ומתחברים "
                                "שוב.", "Google gave no lasting key. Remove JARVES at "
                                "myaccount.google.com/connections, then connect again.", ""),
    "GOOGLE_SCOPE_MISSING": ("לא סימנת את כל ההרשאות. מתחברים שוב ומסמנים את כל התיבות.",
                             "Not every permission was ticked. Connect again and tick every box.", "scopes"),
    "API_DISABLED_CALENDAR": ("Google Calendar API לא מופעל בפרויקט. מפעילים אותו.",
                              "The Google Calendar API is not enabled in the project. Enable it.", "calendar_api"),
    "API_DISABLED_GMAIL": ("Gmail API לא מופעל בפרויקט. מפעילים אותו.",
                           "The Gmail API is not enabled in the project. Enable it.", "gmail_api"),
    "GOOGLE_OFFLINE": ("אין חיבור לגוגל. בודקים את האינטרנט ומנסים שוב.",
                       "Cannot reach Google. Check the internet and try again.", ""),
    "GOOGLE_NO_CLIENT": ("עוד אין מפתח מגוגל. עוברים על השלבים ומורידים את הקובץ (Download JSON).",
                         "No Google client yet. Follow the steps and download the file (Download JSON).", "client"),
}


def fix_for(code: str) -> dict:
    he, en, step = FIXES.get(code, ("משהו השתבש. מריצים שוב " + START_COMMAND + ".",
                                    "Something went wrong. Run " + START_COMMAND + " again.", ""))
    return {"code": code, "he": he, "en": en, "step": step, "url": step_url(step) if step else ""}


# ---------------------------------------------------------------------- saved progress
def _state_file(home: Path) -> Path:
    return home / "google" / "setup.json"


def load_state(home: Path) -> dict:
    d = _read_json(_state_file(home))
    d.setdefault("steps", {})
    return d


def save_state(home: Path, st: dict) -> None:
    _write_private(_state_file(home), st)            # owner-only: may hold the user's address (never served)


def mark_step(home: Path, step: str, done: bool = True) -> dict:
    if step not in STEP_IDS:
        raise KeyError(step)
    st = load_state(home)
    if done:
        st["steps"][step] = time.time()
    else:
        st["steps"].pop(step, None)
    save_state(home, st)
    return st


def step_url(step_id: str, project: str = "") -> str:
    s = next(x for x in STEPS if x["id"] == step_id)
    url = CONSOLE + s["url"]
    if project and step_id != "project":
        url += ("&" if "?" in url else "?") + "project=" + project
    return url


def scopes_text(calendar_only: bool = False) -> str:
    return ",".join(CALENDAR_SCOPES if calendar_only else ALL_SCOPES)


# ---------------------------------------------------------------------- small platform helpers
def copy_to_clipboard(text: str) -> bool:
    """Best effort; False when no clipboard tool exists (the value is then printed to copy by hand)."""
    try:
        if sys.platform == "win32":
            subprocess.run(["clip"], input=text.encode("utf-16"), check=True, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
        for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=5)
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def downloads_dir() -> Path:
    return Path.home() / "Downloads"


def find_client_json(dirs, since: float = 0.0) -> Path | None:
    """Newest client_secret*.json in `dirs` modified at/after `since` (a browser may still be writing: size > 0)."""
    hits = []
    for d in dirs:
        try:
            for p in Path(d).glob("client_secret*.json"):
                st = p.stat()
                if st.st_mtime >= since - 2 and st.st_size > 0:
                    hits.append((st.st_mtime, p))
        except OSError:
            continue
    return max(hits)[1] if hits else None


def take_client_file(auth: GoogleAuth, path: Path) -> None:
    """Validate + store the client privately, then remove the download (it holds the client secret)."""
    auth.import_client(path)
    if Path(path).resolve() != auth.client_file.resolve():
        try:
            Path(path).unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------- live self-check (read-only)
async def self_check(auth: GoogleAuth, *, gmail: bool = True,
                     transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Calendar list (1 entry) and Gmail profile. Never stores or returns names, ids or the address."""
    out: dict = {"at": time.time(), "calendar": "not_run", "gmail": "not_run" if gmail else "skipped", "error": ""}

    async def probe(name: str, url: str, need, params=None) -> None:
        try:
            token = await auth.access_token(need)
            async with httpx.AsyncClient(timeout=10, transport=transport or auth._transport) as c:
                r = await c.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        except GoogleAuthError as e:
            out[name], out["error"] = "fail", out["error"] or e.code
            return
        except httpx.HTTPError:
            out[name], out["error"] = "fail", out["error"] or "GOOGLE_OFFLINE"
            return
        if r.status_code == 200:
            out[name] = "ok"
            return
        reason = ""
        try:
            err = r.json().get("error", {})
            reason = (err.get("errors") or [{}])[0].get("reason", "") or err.get("status", "")
            reason += " " + json.dumps(err.get("details") or "")[:400]
        except (ValueError, AttributeError):
            pass
        low = reason.lower()
        if r.status_code == 403 and ("accessnotconfigured" in low or "service_disabled" in low):
            code = "API_DISABLED_" + name.upper()
        elif r.status_code == 403 and "insufficient" in low:
            code = "GOOGLE_SCOPE_MISSING"
        elif r.status_code == 401:
            code = "GOOGLE_AUTH_EXPIRED"
        else:
            code = f"GOOGLE_API_{r.status_code}"
        out[name], out["error"] = "fail", out["error"] or code
    await probe("calendar", CAL_LIST, CALENDAR_SCOPES, {"maxResults": 1})
    if gmail:
        await probe("gmail", GMAIL_PROFILE, (GMAIL_READ,))
    out["ok"] = out["calendar"] == "ok" and out["gmail"] in ("ok", "skipped")
    if out["error"]:
        out["fix"] = fix_for(out["error"])
    logs.info("google", "GOOGLE_SELF_CHECK", "Google self-check " + ("passed" if out["ok"] else "failed"),
              calendar=out["calendar"], gmail=out["gmail"], error=out["error"])
    return out


def enable_features(home: Path, cfg, *, gmail: bool, send: bool) -> list[str]:
    from .features import Features
    f = Features(cfg, home)
    on = ["google_calendar"] + (["gmail_read"] if gmail else []) + (["gmail_send"] if send else [])
    for fid in on:
        f.set(fid, True, by="google_setup")
    return on


def testing_days_left(connected_at: float, now: float | None = None) -> float | None:
    if not connected_at:
        return None
    return round(max(0.0, TESTING_DAYS - ((now or time.time()) - connected_at) / 86400), 1)


def summary_he(scopes: list[str], gmail_send: bool) -> str:
    cal = any("calendar" in s for s in scopes)
    mail = "gmail.readonly" in scopes
    can = (["לקרוא את כל היומנים שלך"] if cal else []) + (["לחפש ולקרוא מיילים (אני שואל לפני שאני מקריא)"]
                                                         if mail else [])
    ex = [e for e in EXAMPLES_HE if mail or "מייל" not in e]
    return ("מעולה, גוגל מחובר. מעכשיו אני יכול " + " ו".join(can) + ". " +
            "נסה להגיד: " + ", ".join(f"\"{e}\"" for e in ex) + ". " +
            ("שליחת מיילים פועלת: אני תמיד מקריא את הטיוטה ומחכה לאישור שלך. " if gmail_send else
             "שליחת מיילים כבויה; אפשר להדליק אותה בדף הניהול, בעמוד Features. ") +
            "פעם בשבוע גוגל מנתק אפליקציות במצב בדיקה; אז פשוט מריצים שוב " + START_COMMAND + ".")


# ---------------------------------------------------------------------- the wizard
class Wizard:
    """Interactive, resumable. Every outside effect is injectable (tests: fake Downloads, fake browser, fake Google)."""

    def __init__(self, home: Path, cfg=None, *, auth: GoogleAuth | None = None, ask: Callable[[str], str] = input,
                 say: Callable[[str], None] = print, open_url: Callable[[str], bool] | None = None,
                 clipboard: Callable[[str], bool] | None = None, downloads: Path | None = None,
                 sleep: Callable[[float], None] = time.sleep, which: Callable[[str], str | None] | None = None,
                 run: Callable | None = None, calendar_only: bool = False, send: bool = False,
                 client: Path | None = None, restart: bool = False, watch_timeout_s: float = WATCH_TIMEOUT_S,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.home, self.cfg = home, cfg
        self.auth = auth or GoogleAuth(home)
        # resolved now, not at import time, so a test can replace the module-level helpers
        self.ask, self.say = ask, say
        self.open_url, self.clipboard = open_url or webbrowser.open, clipboard or copy_to_clipboard
        self.downloads = downloads or downloads_dir()
        self.sleep, self.which, self.run_cmd = sleep, which or shutil.which, run or subprocess.run
        self.calendar_only, self.send, self.client_path, self.restart = calendar_only, send, client, restart
        self.watch_timeout_s, self.transport = watch_timeout_s, transport
        self.state = load_state(home)

    # --- output helpers
    def _h(self, he: str, en: str = "") -> None:
        self.say(he)
        if en:
            self.say("   " + en)

    def _save(self) -> None:
        save_state(self.home, self.state)

    def _choice(self, prompt: str) -> str:
        try:
            return (self.ask(prompt) or "").strip().lower()
        except EOFError:                              # no terminal input: stop safely (progress is saved)
            return "q"

    def _text(self, prompt: str) -> str:
        try:
            return self.ask(prompt) or ""
        except EOFError:
            return ""

    @property
    def scopes(self):
        return CALENDAR_SCOPES if self.calendar_only else ALL_SCOPES

    def steps(self) -> list[dict]:
        return [s for s in STEPS if not (self.calendar_only and s.get("gmail"))]

    # --- main
    def run(self) -> int:
        try:
            return self._run()
        except KeyboardInterrupt:
            self._save()
            self._h("\nעצרנו. ההתקדמות נשמרה; מריצים שוב " + START_COMMAND + " וממשיכים מאותו מקום.",
                    "Stopped. Progress is saved; run " + START_COMMAND + " again to continue.")
            return 130

    def _run(self) -> int:
        if self.restart:
            self.state = {"steps": {}}
        self.state["calendar_only"] = self.calendar_only
        self.state.setdefault("started_at", time.time())
        self._save()
        self._h("JARVES: חיבור גוגל (יומן" + ("" if self.calendar_only else " + ג'ימייל") + ")",
                "JARVES: connect Google (Calendar" + ("" if self.calendar_only else " + Gmail") + ")")
        st = self.auth.status()
        if st["connected"]:
            self._h("גוגל כבר מחובר. בודק שהכל עובד...", "Google is already connected. Checking it works...")
            return self._finish()
        if st["needs_reconnect"]:
            self._h("החיבור לגוגל פג (גוגל מנתק כל 7 ימים במצב Testing). רק מתחברים שוב, דקה אחת.",
                    "The Google connection expired (every 7 days in Testing mode). Just signing in again.")
        if not self._have_client():
            if not self._console_steps():
                return 1
            if not self._wait_for_client():
                return 1
        return self._login()

    def _have_client(self) -> bool:
        if self.client_path:
            return self._take(Path(self.client_path))
        if self.auth.client():
            return True
        found = find_client_json([self.auth.dir, self.home, self.downloads])   # downloaded earlier: use it
        if found is not None:
            self._h("מצאתי קובץ מפתח שכבר הורדת: " + found.name, "Found a client file you downloaded: " + found.name)
            return self._take(found)
        return False

    def _take(self, path: Path) -> bool:
        try:
            take_client_file(self.auth, path)
        except GoogleAuthError as e:
            fx = fix_for(e.code)
            self._h("הקובץ " + path.name + " לא מתאים. " + fx["he"], fx["en"])
            if fx["url"]:
                self.open_url(fx["url"])
            return False
        self._h("לקחתי את המפתח ושמרתי אותו בתיקייה פרטית (והוצאתי אותו מההורדות).",
                "Took the client file and stored it privately (removed from Downloads).")
        self.state["steps"]["client"] = time.time()
        self._save()
        return True

    # --- console steps
    def _email(self) -> str:
        if not self.state.get("email"):
            e = (self._text("מה כתובת הג'ימייל שלך? (נשמרת רק במחשב הזה; Enter לדלג)  Your Gmail address: ") or "").strip()
            if "@" in e:
                self.state["email"] = e
                self._save()
        return self.state.get("email", "")

    def _gcloud(self) -> None:
        """Optional: create the project + enable both APIs with an INSTALLED gcloud, only after a yes."""
        if "project" in self.state["steps"]:
            return
        exe = self.which("gcloud")
        if not exe:
            return
        a = self._choice("gcloud מותקן אצלך. ליצור את הפרויקט ולהפעיל את ה-APIs אוטומטית? (y = כן, Enter = לא)  "
                         "gcloud is installed: create the project and enable the APIs for you? [y/N] ")
        if a not in ("y", "yes", "כ", "כן"):
            return
        pid = f"jarves-home-{secrets.randbelow(10**6):06d}"
        apis = ["calendar-json.googleapis.com"] + ([] if self.calendar_only else ["gmail.googleapis.com"])
        for args, label in (([exe, "projects", "create", pid, f"--name={PROJECT_NAME}"], "project"),
                            ([exe, "services", "enable", *apis, "--project", pid], "apis")):
            try:
                r = self.run_cmd(args, capture_output=True, text=True, timeout=180)
                ok = r.returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
            if not ok:
                self._h("gcloud לא הצליח (אולי צריך קודם gcloud auth login). ממשיכים ידנית, זה פשוט.",
                        "gcloud failed (maybe run `gcloud auth login` first). Continuing by hand; it is easy.")
                return
            if label == "project":
                self.state["project_id"] = pid
                self.state["steps"]["project"] = time.time()
            else:
                self.state["steps"]["calendar_api"] = time.time()
                if not self.calendar_only:
                    self.state["steps"]["gmail_api"] = time.time()
            self._save()
        self._h("gcloud יצר את הפרויקט " + pid + " והפעיל את ה-APIs.",
                "gcloud created project " + pid + " and enabled the APIs.")

    def _console_steps(self) -> bool:
        steps = self.steps()
        self._h("עוברים על " + str(len(steps)) + " שלבים קצרים בדפדפן. אני פותח כל דף ומעתיק לך מה להדביק.",
                f"{len(steps)} short steps in the browser. I open each page and copy what to paste.")
        self._email()
        self._gcloud()
        for i, s in enumerate(steps, 1):
            if s["id"] in self.state["steps"]:
                self._h(f"[{i}/{len(steps)}] ✓ {s['title_he']} (כבר נעשה)", f"done earlier: {s['title_en']}")
                continue
            url = step_url(s["id"], self.state.get("project_id", ""))
            self.say("")
            self._h(f"[{i}/{len(steps)}] {s['title_he']}", s["title_en"])
            for he, en in zip(s["clicks_he"], s["clicks_en"]):
                self._h(f"  • {he}", f"  ({en})")
            value = s["copy"].replace("{scopes}", scopes_text(self.calendar_only)).replace(
                "{email}", self.state.get("email", ""))
            if value:
                if self.clipboard(value):
                    self._h("  הועתק ללוח, מדביקים עם Ctrl+V", "  Copied to the clipboard: paste with Ctrl+V")
                else:
                    self._h("  להעתיק: " + value, "  Copy this: " + value)
            self.open_url(url)
            self._h("  הדף נפתח בדפדפן: " + url, "")
            a = self._choice("  Enter = סיימתי · s = לדלג · q = לעצור ולהמשיך אחר כך   (Enter = done, s = skip, "
                             "q = stop) ")
            if a in ("q", "quit", "exit"):
                self._save()
                self._h("עצרנו. מריצים שוב " + START_COMMAND + " וממשיכים מכאן.",
                        "Stopped. Run " + START_COMMAND + " again to continue from here.")
                return False
            if a in ("s", "skip"):
                continue
            if s["id"] == "project" and not self.state.get("project_id"):
                pid = (self._text("  אם רואים את ה-Project ID, אפשר להדביק אותו (Enter לדלג)  Project ID (optional): ")
                       or "").strip()
                if pid and all(c.isalnum() or c == "-" for c in pid):
                    self.state["project_id"] = pid
            self.state["steps"][s["id"]] = time.time()
            self._save()
        return True

    def _wait_for_client(self) -> bool:
        since = self.state.get("started_at", time.time())
        self._h("מחכה לקובץ client_secret...json בתיקיית ההורדות (" + str(self.downloads) + "). Ctrl+C לעצור.",
                "Waiting for client_secret...json in Downloads. Ctrl+C to stop.")
        deadline = time.monotonic() + self.watch_timeout_s
        while time.monotonic() < deadline:
            p = find_client_json([self.downloads, self.auth.dir, self.home], since)
            if p is not None:
                return self._take(p)
            self.sleep(1.0)
        self._h("לא הגיע קובץ. בחלון של גוגל לוחצים Download JSON, ואז מריצים שוב " + START_COMMAND +
                " (או: " + START_COMMAND + " --client <הקובץ>).",
                "No file arrived. Click Download JSON in Google's dialog, then run " + START_COMMAND +
                " again (or pass --client <file>).")
        return False

    # --- sign-in, check, features, summary
    def _login(self) -> int:
        self.say("")
        self._h("עכשיו נפתח דפדפן להתחברות. בוחרים את החשבון שלך.", "Opening the browser to sign in. Pick your account.")
        self._h("גוגל יגיד 'Google hasn't verified this app': זו האפליקציה שלך, לוחצים Continue.",
                "Google says 'Google hasn't verified this app': it is your own app, click Continue.")
        self._h("מסמנים את כל התיבות ולוחצים Continue.", "Tick every box and click Continue.")
        for attempt in (1, 2):
            try:
                st = asyncio.run(self.auth.sign_in(self.scopes))
                break
            except GoogleAuthError as e:
                fx = fix_for(e.code)
                self.state["last_error"] = e.code
                self._save()
                self._h("לא הצלחנו: " + fx["he"], fx["en"])
                if fx["url"]:
                    self.open_url(step_url(fx["step"], self.state.get("project_id", "")))
                    self._h("  פתחתי את הדף לתיקון: " + fx["url"], "")
                if attempt == 2 or self._choice("  Enter = לנסות שוב · q = לעצור   (Enter = retry, q = stop) ") \
                        in ("q", "quit", "exit"):
                    return 1
        missing = [short_scope(s) for s in self.scopes if short_scope(s) not in st["scopes"]]
        if missing:
            fx = fix_for("GOOGLE_SCOPE_MISSING")
            self._h(fx["he"] + " (" + ", ".join(missing) + ")", fx["en"])
            if not st["calendar"]:
                return 1
        return self._finish()

    def _finish(self) -> int:
        gmail = not self.calendar_only and self.auth.status()["gmail_read"]
        chk = asyncio.run(self_check(self.auth, gmail=gmail, transport=self.transport))
        self.state["check"] = {k: chk[k] for k in ("at", "calendar", "gmail", "error")}
        if chk["ok"]:
            self.state.pop("last_error", None)
        else:
            self.state["last_error"] = chk["error"]
        self._save()
        self._h("בדיקה: יומן " + ("✓" if chk["calendar"] == "ok" else "✗") +
                ("" if chk["gmail"] == "skipped" else ", ג'ימייל " + ("✓" if chk["gmail"] == "ok" else "✗")),
                f"check: calendar {chk['calendar']}, gmail {chk['gmail']}")
        if not chk["ok"]:
            fx = chk["fix"]
            self._h(fx["he"], fx["en"])
            if fx["url"]:
                self.open_url(step_url(fx["step"], self.state.get("project_id", "")))
            self._h("אחרי התיקון מריצים שוב " + START_COMMAND + ".", "After the fix run " + START_COMMAND + " again.")
            return 1
        on = enable_features(self.home, self.cfg, gmail=gmail, send=self.send and gmail)
        self.state["completed_at"] = time.time()
        self._save()
        st = self.auth.status()
        self.say("")
        self.say(summary_he(st["scopes"], "gmail_send" in on))
        self.say("   (features on: " + ", ".join(on) + ")")
        self._h("אם JARVES כבר רץ ולא רואה את היומן: jarvis stop ואז jarvis.",
                "If JARVES is already running and does not see it yet: jarvis stop, then jarvis.")
        return 0


# ---------------------------------------------------------------------- the checklist (dashboard)
_LOGIN: dict = {"running": False, "started": 0.0, "error": "", "done": 0.0}
_LOGIN_LOCK = threading.Lock()


def checklist(home: Path) -> dict:
    """Everything the Connections page shows. Reads local files only; never contacts Google, never an address."""
    auth = GoogleAuth(home)
    st, ss = auth.status(), load_state(home)
    pid = ss.get("project_id", "")
    cal_only = bool(ss.get("calendar_only"))
    steps = [{"id": s["id"], "title_he": s["title_he"], "title_en": s["title_en"], "clicks_he": s["clicks_he"],
              "clicks_en": s["clicks_en"], "url": step_url(s["id"], pid), "gmail": bool(s.get("gmail")),
              "copy": s["copy"].replace("{scopes}", scopes_text(cal_only)).replace("{email}", ""),
              "done": s["id"] in ss["steps"] or (s["id"] == "client" and st["client_configured"])}
             for s in STEPS]
    chk = (ss.get("check") or {}) if st["connected"] else {}      # an old result says nothing after a disconnect
    err =st["last_error"] or ss.get("last_error", "")
    days = testing_days_left(st["connected_at"]) if st["connected"] else None
    if st["connected"] and chk.get("calendar") == "ok":
        nxt_he, nxt_en, state = "הכל מחובר. אפשר לשאול: \"מה יש לי מחר ביומן?\"", \
            "All connected. Try: \"What's on my calendar tomorrow?\"", "connected"
    elif st["connected"]:
        nxt_he, nxt_en, state = "מחובר. לוחצים Test כדי לוודא שזה עובד.", "Connected. Press Test to check it works.", \
            "connected"
    elif st["needs_reconnect"]:
        nxt_he, nxt_en, state = "החיבור פג (כל 7 ימים). לוחצים Reconnect או מריצים " + START_COMMAND + ".", \
            "The connection expired (every 7 days). Press Reconnect or run " + START_COMMAND + ".", "needs_reconnect"
    elif st["client_configured"]:
        nxt_he, nxt_en, state = "המפתח מגוגל נמצא. לוחצים Start כדי להתחבר.", \
            "The Google client file is here. Press Start to sign in.", "ready_to_sign_in"
    else:
        nxt_he, nxt_en, state = "מריצים במחשב: " + START_COMMAND + " (הוא מדריך צעד אחר צעד), או עוברים על השלבים כאן.", \
            "Run " + START_COMMAND + " on this computer (it guides you step by step), or follow the steps here.", \
            "not_connected"
    with _LOGIN_LOCK:
        login = dict(_LOGIN)
    return {"state": state, "next_he": nxt_he, "next_en": nxt_en, "steps": steps, "project_id": pid,
            "calendar_only": cal_only, "client_configured": st["client_configured"], "signed_in": st["connected"],
            "needs_reconnect": st["needs_reconnect"], "scopes": st["scopes"], "calendar": st["calendar"],
            "gmail_read": st["gmail_read"], "gmail_send": st["gmail_send"],
            "check": {"calendar": chk.get("calendar", "not_run"), "gmail": chk.get("gmail", "not_run"),
                      "at": chk.get("at", 0)},
            "last_error": err, "fix": fix_for(err) if err else None, "connected_at": st["connected_at"],
            "testing_days_left": days, "start_command": START_COMMAND, "gcloud_installed": bool(shutil.which("gcloud")),
            "login": login, "message_he": st["message_he"],
            "explain_en": "JARVES uses your own free Google Cloud app, so only you have access. The keys stay on "
                          "this computer. While the app is in Testing mode Google disconnects it every 7 days; "
                          "then press Reconnect (one minute).",
            "explain_he": "JARVES משתמש באפליקציה פרטית שלך בגוגל קלאוד (חינם), כך שרק לך יש גישה. המפתחות נשמרים "
                          "רק במחשב הזה. במצב Testing גוגל מנתק כל 7 ימים; אז לוחצים Reconnect (דקה)."}


def _login_thread(home: Path, scopes, open_browser) -> None:
    auth = GoogleAuth(home, open_browser=open_browser)
    err = ""
    try:
        asyncio.run(auth.sign_in(scopes))
    except GoogleAuthError as e:
        err = e.code
    except Exception as e:                            # never let a thread die silently
        err = type(e).__name__
    st = load_state(home)
    if err:
        st["last_error"] = err
    else:
        st.pop("last_error", None)
    save_state(home, st)
    with _LOGIN_LOCK:
        _LOGIN.update(running=False, error=err, done=time.time())


def start_login(home: Path, *, open_browser=webbrowser.open, background: bool = True) -> dict:
    """Dashboard Start / Reconnect: take a downloaded client file if needed, then sign in (browser) in a thread."""
    auth = GoogleAuth(home)
    if not auth.client():
        p = find_client_json([auth.dir, home, downloads_dir()])
        if p is None:
            return {"started": False, "fix": fix_for("GOOGLE_NO_CLIENT")}
        try:
            take_client_file(auth, p)
        except GoogleAuthError as e:
            return {"started": False, "fix": fix_for(e.code)}
    with _LOGIN_LOCK:
        if _LOGIN["running"] and time.time() - _LOGIN["started"] < 330:
            return {"started": False, "running": True}
        _LOGIN.update(running=True, started=time.time(), error="")
    scopes = CALENDAR_SCOPES if load_state(home).get("calendar_only") else ALL_SCOPES
    if background:
        threading.Thread(target=_login_thread, args=(home, scopes, open_browser), name="jarves-google-login",
                         daemon=True).start()
    else:
        _login_thread(home, scopes, open_browser)
    return {"started": True}


def run_check(home: Path, cfg=None, transport=None) -> dict:
    """Dashboard Test: the live self-check; on success also makes sure the features are on (send stays as is)."""
    auth = GoogleAuth(home, transport=transport)
    gmail = auth.status()["gmail_read"] and not load_state(home).get("calendar_only")
    chk = asyncio.run(self_check(auth, gmail=gmail, transport=transport))
    st = load_state(home)
    st["check"] = {k: chk[k] for k in ("at", "calendar", "gmail", "error")}
    if chk["ok"]:
        st.pop("last_error", None)
        enable_features(home, cfg, gmail=gmail, send=False)
    else:
        st["last_error"] = chk["error"]
    save_state(home, st)
    return chk


def dashboard_routes(register) -> None:
    """Connections page: GET /api/google/setup (checklist), POST /api/google/start (sign in / reconnect),
    POST /api/google/test (live read-only check), POST /api/google/step/<id> {done} (tick a console step)."""
    def get(ctx, _m, _b):
        return 200, checklist(ctx.home)

    def start(ctx, _m, _b):
        r = start_login(ctx.home)
        if not r.get("started"):
            r["error"] = (r["fix"]["he"] + " · " + r["fix"]["en"]) if r.get("fix") else "a sign-in is already waiting"
        return (200 if r.get("started") else 409), r

    def test(ctx, _m, _b):
        if not GoogleAuth(ctx.home).status()["connected"]:
            return 409, {"error": "Google is not connected", "fix": fix_for("GOOGLE_NO_CLIENT")}
        return 200, run_check(ctx.home, ctx.cfg)

    def step(ctx, m, body):
        try:
            mark_step(ctx.home, m.group(1), bool((body or {}).get("done", True)))
        except KeyError:
            return 404, {"error": "no such step"}
        return 200, checklist(ctx.home)
    register("GET", r"/api/google/setup", get)
    register("POST", r"/api/google/start", start)
    register("POST", r"/api/google/test", test)
    register("POST", r"/api/google/step/([a-z_]+)", step)


# ---------------------------------------------------------------------- CLI
def cmd_start(a, cfg, home) -> int:
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    return Wizard(home, cfg, calendar_only=bool(getattr(a, "calendar_only", False)),
                  send=bool(getattr(a, "send", False)), client=Path(a.client) if getattr(a, "client", None) else None,
                  downloads=Path(a.downloads) if getattr(a, "downloads", None) else None,
                  restart=bool(getattr(a, "restart", False))).run()
