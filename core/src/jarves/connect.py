"""`jarvis connect`: ONE guided command for every external account (owner request 20 Oct 2026, Issue #17).

"Setup of external accounts must be as automatic as possible; the command should guide me." It checks what is
connected (Google Calendar + Gmail, Telegram, the Pear Desktop API plugin, the wake word, and optionally Instagram
and Home Assistant), walks the owner through ONLY what is missing, and REUSES the existing wizards (`jarvis google
start`, `jarvis telegram start`, the Instagram / Home Assistant wizards, `jarvis music pair`, `jarvis wake test`).
What it adds is automation around them:

- opens the exact page in the browser (t.me/BotFather, the Telegram deep link, Google Cloud pages) and puts the next
  value to paste on the clipboard;
- DETECTS completion instead of asking: a Telegram bot token copied from BotFather is found by watching the
  clipboard (only with the owner's consent; the clipboard is cleared once the token is used), the Google
  client_secret*.json by watching Downloads (google_setup), and the Telegram chat by long-polling getUpdates until
  the owner taps Start on a one-time deep link (only the chat that sent the code is paired), then a confirmation
  message is sent;
- validates every credential live before it is saved (getMe, the Google self-check, Pear's own auth);
- is resumable and safe to re-run (it only does what is missing; the sub-wizards keep their own progress);
- a typed secret is hidden (getpass) and only the fallback when the clipboard watch is declined or finds nothing.

Exit code: 0 unless a step really FAILED (a credential was rejected, a wizard errored). Something the owner still has
to do outside (start Pear Desktop, calibrate the wake word) is "needs attention", not a failure. Ctrl+C = 130 and the
progress is kept.

Also here: the once-a-day reminder (`maybe_nudge`) and the dashboard Connections checklist (GET /api/connect).
Every outside effect (clipboard, browser, Downloads, Telegram/Google/Pear endpoints, the wake runner) is injectable.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol
from urllib.parse import urlsplit

from . import features as feat
from . import logs
from . import clilang
from .channels_common import StepSkipped, is_skip_word, read_json, read_secret, targets, write_private_json

FEATURE = "guided_setup"
COMMAND = "jarvis connect"
STATE_FILE = "connect.json"
ONLY = ("google", "telegram", "pear", "wake", "instagram", "home")
BOTFATHER_URL = "https://t.me/BotFather"
TOKEN_RE = re.compile(r"(?<![\w-])\d{5,15}:[A-Za-z0-9_-]{30,80}(?![\w-])")     # a Telegram bot token
NUDGE_EVERY_S = 24 * 3600
NUDGE_CHECK_EVERY_S = 900
NUDGE_TEXT_HE = "לא חיברת עדיין גוגל, להריץ jarvis connect?"
NUDGE_TEXT_RECONNECT_HE = "החיבור לגוגל פג. להריץ jarvis connect?"

# result / status words (state -> Hebrew, English)
WORDS = {"connected": ("מחובר", "connected"), "missing": ("חסר", "not connected"),
         "attention": ("דורש טיפול", "needs attention"), "skipped": ("דולג", "skipped"),
         "failed": ("נכשל", "failed")}


@dataclass
class Item:
    id: str
    title_he: str
    title_en: str
    optional: bool
    state: str = "missing"            # connected | missing | attention  (a run adds skipped | failed)
    detail_he: str = ""
    detail_en: str = ""
    fix_he: str = ""
    fix_en: str = ""


TITLES = {
    "google": ("גוגל (יומן + ג'ימייל)", "Google (Calendar + Gmail)", False),
    "telegram": ("טלגרם", "Telegram", False),
    "pear": ("Pear Desktop (תוסף API Server)", "Pear Desktop (API Server plugin)", False),
    "wake": ("מילת ההפעלה (כיול לקול שלך)", "Wake word (calibrated to your voice)", True),
    "instagram": ("אינסטגרם (לא חובה)", "Instagram (optional)", True),
    "home": ("Home Assistant (לא חובה)", "Home Assistant (optional)", True),
}
# what the first prompt of a step asks about (the Continue / Skip / Never line follows it)
QUESTIONS = {
    "wake": ("לכייל עכשיו את מילת ההפעלה לקול שלך (כ-20 שניות, אומרים Hey Jarvis כמה פעמים)?",
             "Calibrate the wake word to your voice now (about 20 seconds, say Hey Jarvis a few times)?"),
    "instagram": ("לחבר עכשיו אינסטגרם?", "Connect Instagram now?"),
    "home": ("לחבר עכשיו Home Assistant?", "Connect Home Assistant now?"),
}


# ---------------------------------------------------------------------- the clipboard
class Clipboard(Protocol):
    """What `connect` needs from a clipboard. Tests use channels_fakes.FakeClipboard."""

    def read(self) -> str | None: ...

    def write(self, text: str) -> bool: ...

    def clear(self) -> bool: ...


class SystemClipboard:
    """The OS clipboard: Windows through user32 (no process per poll), else pbpaste / wl-paste / xclip."""

    def read(self) -> str | None:
        try:
            if sys.platform == "win32":
                return _win_clipboard_read()
            for cmd in (["pbpaste"], ["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"]):
                if shutil.which(cmd[0]):
                    return subprocess.run(cmd, capture_output=True, timeout=3).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def write(self, text: str) -> bool:
        from .google_setup import copy_to_clipboard
        return copy_to_clipboard(text)

    def clear(self) -> bool:
        try:
            if sys.platform == "win32":
                return _win_clipboard_clear()
            for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
                if shutil.which(cmd[0]):
                    subprocess.run(cmd, input=b"", check=True, timeout=3)
                    return True
        except (OSError, subprocess.SubprocessError):
            pass
        return False


def _win_clipboard_read() -> str | None:                                   # pragma: windows
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.OpenClipboard.argtypes, u32.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    u32.GetClipboardData.argtypes, u32.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
    k32.GlobalLock.argtypes, k32.GlobalLock.restype = [wintypes.HGLOBAL], wintypes.LPVOID
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not u32.OpenClipboard(None):
        return None
    try:
        h = u32.GetClipboardData(13)                                        # CF_UNICODETEXT
        if not h:
            return ""
        p = k32.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def _win_clipboard_clear() -> bool:                                        # pragma: windows
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    u32.OpenClipboard.argtypes, u32.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    if not u32.OpenClipboard(None):
        return False
    try:
        return bool(u32.EmptyClipboard())
    finally:
        u32.CloseClipboard()


# ---------------------------------------------------------------------- local status (no network, no changes)
def pear_up(url: str, timeout: float = 0.4) -> bool:
    """Is something listening where the Pear API Server plugin lives? (a TCP connect to localhost only)"""
    u = urlsplit(url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 26538), timeout=timeout):
            return True
    except OSError:
        return False


def wake_state(home: Path) -> tuple[bool, str]:
    """Calibrated to the owner's voice = a saved mic calibration (`jarvis wake test`), a trained phrase, or at least
    three of the owner's own recordings in <state_dir>/wake/samples."""
    from .voice import wake_calib
    w = Path(home) / "wake"
    try:
        if wake_calib.load_all(home):
            return True, "mic calibration saved"
    except Exception:
        pass
    if list(w.glob("*.npz")):
        return True, "trained phrase"
    if len(list((w / "samples").glob("*.wav"))) >= 3:
        return True, "your recordings"
    return False, ""


def state_file(home: Path) -> Path:
    return Path(home) / STATE_FILE


def last_run(home: Path) -> dict:
    return read_json(state_file(home))


def status_items(home: Path, cfg=None, *, pear_probe: Callable[[str], bool] = pear_up) -> list[Item]:
    """The checklist: local files and one localhost connect only. Used by `--status` and the dashboard."""
    from . import homeassistant as ha
    from . import instagram_channel as ig
    from . import telegram_channel as tg
    from .google_auth import GoogleAuth
    home = Path(home)
    items = {i: Item(i, *TITLES[i]) for i in ONLY}

    g, it = GoogleAuth(home).status(), items["google"]
    if g["connected"]:
        it.state = "connected"
        it.detail_he, it.detail_en = "מחובר", "connected (" + ", ".join(g["scopes"]) + ")"
    elif g["needs_reconnect"]:
        it.state, it.detail_he = "attention", "החיבור פג (גוגל מנתק כל 7 ימים במצב Testing)"
        it.detail_en = "the connection expired (Google disconnects every 7 days in Testing mode)"
        it.fix_he, it.fix_en = f"מריצים {COMMAND} והוא מתחבר מחדש", f"Run {COMMAND}: it signs in again"
    else:
        it.detail_he, it.detail_en = "לא מחובר", "not connected"
        it.fix_he, it.fix_en = f"מריצים {COMMAND}", f"Run {COMMAND}"

    t, it = tg.status(home), items["telegram"]
    if t["token_saved"] and t["chats"]:
        it.state, it.detail_he, it.detail_en = "connected", f"בוט @{t['bot']} וצ'אט מחוברים", f"bot @{t['bot']} paired"
    elif t["token_saved"]:
        it.state, it.detail_he, it.detail_en = "attention", "הטוקן שמור אבל הצ'אט לא מחובר", "token saved, no chat paired"
        it.fix_he, it.fix_en = f"מריצים {COMMAND} ולוחצים Start בבוט", f"Run {COMMAND} and press Start in the bot"
    else:
        it.detail_he, it.detail_en = "לא מחובר", "not connected"
        it.fix_he, it.fix_en = f"מריצים {COMMAND}", f"Run {COMMAND}"

    it = items["pear"]
    url = getattr(getattr(cfg, "music", None), "pear_url", "http://127.0.0.1:26538")
    if not pear_probe(url):
        it.state = "attention"
        it.detail_he, it.detail_en = f"Pear לא עונה ב-{urlsplit(url).netloc}", f"Pear does not answer at {urlsplit(url).netloc}"
        it.fix_he, it.fix_en = PEAR_STEPS_HE, PEAR_STEPS_EN
    elif (home / "pear_client_id.paired").exists():
        it.state, it.detail_he, it.detail_en = "connected", "Pear עונה ומחובר", "Pear answers and is paired"
    else:
        it.detail_he, it.detail_en = "Pear עונה אבל עוד לא אישרתם Allow", "Pear answers but you have not pressed Allow yet"
        it.fix_he, it.fix_en = f"מריצים {COMMAND} ולוחצים Allow בחלון של Pear", f"Run {COMMAND} and press Allow in Pear's window"

    ok, why = wake_state(home)
    it = items["wake"]
    if ok:
        it.state, it.detail_he, it.detail_en = "connected", "מכוייל לקול שלך", f"calibrated ({why})"
    else:
        it.detail_he, it.detail_en = "לא מכוייל לקול שלך", "not calibrated to your voice"
        it.fix_he, it.fix_en = "jarvis wake test --apply (20 שניות, אומרים Hey Jarvis)", \
            "jarvis wake test --apply (20 seconds, say Hey Jarvis)"

    i, it = ig.status(home), items["instagram"]
    if i["token_saved"] and i["verified"]:
        it.state, it.detail_he, it.detail_en = "connected", f"@{i['account']}", f"@{i['account']}"
    else:
        it.detail_he, it.detail_en = "לא מחובר", "not connected"
        it.fix_he, it.fix_en = "jarvis instagram start", "jarvis instagram start"

    it = items["home"]
    if ha.configured(home):
        it.state, it.detail_he, it.detail_en = "connected", "מחובר", "connected"
    else:
        it.detail_he, it.detail_en = "לא מחובר", "not connected"
        it.fix_he, it.fix_en = "jarvis home start", "jarvis home start"
    return [items[i] for i in ONLY]


PEAR_STEPS_HE = ("1. פותחים את Pear Desktop (אפשר: jarvis music open). 2. בתפריט של Pear: Plugins > API Server וסימון V "
                 "ליד API Server (Hostname 127.0.0.1, Port 26538). 3. מריצים שוב " + COMMAND + ".")
PEAR_STEPS_EN = ("1. Open Pear Desktop (you can run: jarvis music open). 2. In Pear's menu: Plugins > API Server, tick it "
                 "(Hostname 127.0.0.1, Port 26538). 3. Run " + COMMAND + " again.")


def items_json(home: Path, cfg=None, **kw) -> dict:
    items = status_items(home, cfg, **kw)
    return {"items": [asdict(i) for i in items], "command": COMMAND, "last_run": last_run(home).get("last_run", 0),
            "all_connected": all(i.state == "connected" for i in items if not i.optional)}


# ---------------------------------------------------------------------- the guided run
def _ask_eof(_prompt: str) -> str:
    raise EOFError


class _StepIO:
    """The io the steps and sub-wizards see: typing `s` / `skip` at any prompt abandons the step (StepSkipped)."""
    skippable = True

    def __init__(self, inner) -> None:
        self.inner = inner

    @property
    def lines(self):
        return getattr(self.inner, "lines", None)

    def out(self, text: str = "") -> None:
        self.inner.out(text)

    def read(self, prompt: str) -> str | None:
        a = self.inner.read(prompt)
        if is_skip_word(a):
            raise StepSkipped
        return a


def never_list(home: Path) -> list[str]:
    """Items the owner chose 'never ask again' for (setting connect.skipped)."""
    from .userdata import open_db
    if not (Path(home) / "memory.db").exists():                # nothing chosen yet; a status call creates no file
        return []
    db = open_db(home)
    try:
        v = db.setting("connect.skipped")
    finally:
        db.close()
    return [x for x in (v if isinstance(v, list) else []) if x in ONLY]


def set_never(home: Path, ids: list[str], actor: str = "cli") -> None:
    from .userdata import open_db
    db = open_db(home)
    try:
        db.set_setting("connect.skipped", sorted(set(ids)), actor=actor)
    finally:
        db.close()


class Connector:
    def __init__(self, home: Path, cfg, *, io=None, clipboard: Clipboard | None = None,
                 open_url: Callable[[str], bool] | None = None, downloads: Path | None = None, yes: bool = False,
                 only: str | None = None, status_only: bool = False, transport=None,
                 asleep: Callable[[float], Awaitable] = asyncio.sleep, clip_poll_s: float = 1.0,
                 clip_timeout_s: float = 240.0, pear_wait_s: float = 120.0, pear_probe: Callable[[str], bool] = pear_up,
                 google_factory: Callable | None = None, wake_runner: Callable[[], int] | None = None,
                 pair_polls: int = 36, pair_poll_timeout: int = 5, pear_pair_wait_s: float = 90.0,
                 pear_transport=None, optional_runners: dict[str, Callable[[], int]] | None = None,
                 skip: list[str] | None = None, reset_skips: bool = False) -> None:
        self.reset_skips = reset_skips
        from .setup_wizard import ConsoleIO
        self.home, self.cfg = Path(home), cfg
        self.raw_io = io or ConsoleIO()
        self.io = _StepIO(self.raw_io)
        self.skip_now = [x for x in (skip or []) if x in ONLY]
        self.clipboard = clipboard or SystemClipboard()
        self.open_url = open_url or webbrowser.open
        self.downloads, self.yes, self.only, self.status_only = downloads, yes, only, status_only
        self.transport, self.asleep = transport, asleep
        self.clip_poll_s, self.clip_timeout_s, self.pear_wait_s = clip_poll_s, clip_timeout_s, pear_wait_s
        self.pear_probe, self.google_factory, self.wake_runner = pear_probe, google_factory, wake_runner
        self.pair_polls, self.pair_poll_timeout, self.pear_pair_wait_s = pair_polls, pair_poll_timeout, pear_pair_wait_s
        self.pear_transport, self.optional_runners = pear_transport, optional_runners or {}
        self.results: dict[str, str] = {}
        self.notes: dict[str, tuple[str, str]] = {}          # id -> (he, en) next step shown in the summary
        self._clip_token = ""
        self._token_skipped = False

    # --- output / input
    def h(self, he: str, en: str) -> None:
        self.io.out(clilang.fmt(he, en))

    def _yn(self, he: str, en: str, default: bool, eof: bool | None = None) -> bool:
        if self.yes:
            return default
        a = self.io.read(f"{clilang.pick(he, en)} [{'Y/n' if default else 'y/N'}]: ")
        if a is None:
            return default if eof is None else eof
        a = a.strip().lower()
        if not a:
            return default
        return a in ("y", "yes", "כן", "כ", "1")

    # --- run
    def run(self) -> int:
        ids = [self.only] if self.only else list(ONLY)
        self.h("JARVES: חיבור חשבונות מודרך", "JARVES: guided account setup")
        before = {i.id: i for i in status_items(self.home, self.cfg, pear_probe=self.pear_probe)}
        never = never_list(self.home)
        if self.reset_skips and never:
            set_never(self.home, [])
            never = []
            self.h("הדילוגים הקבועים נוקו.", "The 'never ask again' list was cleared.")
        if self.only in never and not self.status_only:       # asking for an item by name brings it back
            never = [x for x in never if x != self.only]
            set_never(self.home, never)
        if self.status_only:
            for fid in ids:
                if fid in never and before[fid].state != "connected":
                    self.results[fid] = "skipped"
                    self.notes[fid] = (f"jarvis connect --only {fid}", f"run {COMMAND} --only {fid}")
            self._summary(ids, before, show_actions=False)
            return 0
        hidden = [f for f in ids if f in never]               # "never ask again": not even mentioned
        ids = [f for f in ids if f not in hidden]
        try:
            for fid in ids:
                it = before[fid]
                if it.state == "connected" and fid not in ("google", "telegram"):
                    self.results[fid] = "connected"
                    continue
                if fid in self.skip_now:
                    self._skipped(fid, "skipped (--skip)")
                    continue
                self.h(f"\n== {it.title_he} ==", f"== {it.title_en} ==")
                if it.state != "connected" and not self._gate(fid):
                    continue
                try:
                    getattr(self, "step_" + fid)(it)
                except StepSkipped:
                    self._finish_clip()
                    self._skipped(fid, "skipped")
        except KeyboardInterrupt:
            self.h("\nעצרנו. מה שכבר חובר נשמר; מריצים שוב " + COMMAND + " וממשיכים.",
                   "Stopped. What is connected is kept; run " + COMMAND + " again to continue.")
            self._finish_clip()
            self._save(ids)
            return 130
        finally:
            self._finish_clip()
        self._save(ids)
        self._summary(ids, {i.id: i for i in status_items(self.home, self.cfg, pear_probe=self.pear_probe)})
        return 1 if "failed" in self.results.values() else 0

    def _skipped(self, fid: str, why: str) -> None:
        self.results[fid] = "skipped"
        self.notes[fid] = (f"jarvis connect --only {fid}", f"run {COMMAND} --only {fid}")
        self.h(f"דילגנו על {TITLES[fid][0]}.", f"Skipped {TITLES[fid][1]}.")

    def _gate(self, fid: str) -> bool:
        """The first prompt of every step: Continue / Skip now / Never ask again. True = go on with the step."""
        if self.yes:
            return True
        q = QUESTIONS.get(fid)
        if q:
            self.h(*q)
        a = (self.raw_io.read("[C]ontinue / [S]kip now / [N]ever ask again (Enter = continue): ") or "").strip().lower()
        if is_skip_word(a, ("s", "skip")):
            self._skipped(fid, "skipped")
            return False
        if is_skip_word(a, ("n", "never", "no")):
            set_never(self.home, never_list(self.home) + [fid])
            self._skipped(fid, "skipped")
            self.h(f"לא אשאל שוב. לחיבור מאוחר יותר: {COMMAND} --only {fid}",
                   f"I will not ask again. To connect later: {COMMAND} --only {fid}  (or {COMMAND} --reset-skips)")
            return False
        return True

    def _save(self, ids: list[str]) -> None:
        old = last_run(self.home)
        items = old.get("items", {}) if isinstance(old.get("items"), dict) else {}
        for fid in ids:
            if fid in self.results:
                items[fid] = {"state": self.results[fid], "at": time.time()}
        try:
            write_private_json(state_file(self.home), {"last_run": time.time(), "items": items})
        except OSError:
            logs.warn("connect", "CONNECT_STATE_NOT_SAVED", "could not save connect.json")

    def _summary(self, ids: list[str], items: dict[str, Item], show_actions: bool = True) -> None:
        rows = []
        for fid in ids:
            it = items[fid]
            state = self.results.get(fid) or it.state
            word = WORDS[state]
            next_he, next_en = self.notes.get(fid) or ((it.fix_he, it.fix_en) if state in ("missing", "attention", "failed") else ("", ""))
            if state == "skipped":
                next_he, next_en = f"דולג (מריצים {COMMAND} --only {fid})", f"(run {COMMAND} --only {fid})"
                word = ("דולג", "skipped")
            title = it.title_en if clilang.current() == "en" else it.title_he if clilang.current() == "he" \
                else f"{it.title_he} / {it.title_en}"
            rows.append((title, clilang.pick(word[0], word[1]), clilang.fmt(next_he, next_en).split("\n")))
        w1 = max(len(r[0]) for r in rows)
        w2 = max(len(r[1]) for r in rows)
        self.io.out(clilang.fmt("\n== סיכום ==", "\n== Summary =="))
        for t, s, nxt in rows:
            self.io.out(f"  {t:<{w1}}  {s:<{w2}}  {nxt[0]}")
            for extra in nxt[1:]:
                self.io.out(f"  {'':<{w1}}  {'':<{w2}}  {extra}")
        if not show_actions:
            self.io.out(clilang.fmt(f"המצב בלבד, שום דבר לא שונה. הרצה: {COMMAND}",
                                    f"Status only, nothing was changed.  Run: {COMMAND}"))

    # --- the clipboard (Telegram token)
    def _finish_clip(self) -> None:
        """Clear the clipboard once the token was used, but only if it still holds it (never wipe other content)."""
        tok, self._clip_token = self._clip_token, ""
        if not tok:
            return
        try:
            if tok in (self.clipboard.read() or ""):
                self.clipboard.clear()
        except Exception:
            logs.warn("connect", "CONNECT_CLIP_CLEAR_FAILED", "could not clear the clipboard")

    async def _token_provider(self) -> str | None:
        self.h("1. נפתח את BotFather בטלגרם. שולחים לו /newbot, בוחרים שם וכתובת (חייבת להסתיים ב-bot).\n"
               "2. כשהוא עונה עם טוקן: לוחצים עליו ו'העתק'. אני מזהה אותו לבד.",
               "1. Opening BotFather in Telegram. Send it /newbot, pick a name and a username (must end in 'bot').\n"
               "2. When it replies with a token: tap it and Copy. I detect it by myself.")
        self.open_url(BOTFATHER_URL)
        self.clipboard.write("/newbot")
        self.h("(העתקתי ללוח את /newbot, אפשר להדביק אותו בשיחה עם BotFather)",
               "(I copied /newbot to the clipboard so you can paste it into the BotFather chat)")
        if self._yn("לעקוב אחרי הלוח עד שמופיע טוקן, ולנקות אותו אחרי השימוש?",
                    "Watch the clipboard until a bot token appears, and clear it after use?", True, eof=False):
            n = max(1, int(self.clip_timeout_s / max(self.clip_poll_s, 0.001)))
            for _ in range(n):
                m = TOKEN_RE.search(self.clipboard.read() or "")
                if m:
                    self._clip_token = m.group(0)
                    self.h("זיהיתי טוקן בלוח. בודק אותו מול טלגרם...", "Found a token on the clipboard. Checking it with Telegram...")
                    return self._clip_token
                await self.asleep(self.clip_poll_s)
            self.h("לא הופיע טוקן בלוח.", "No token appeared on the clipboard.")
        tok = (read_secret(self.io, "Bot token (hidden, Enter = no token now, s = skip this step): ") or "").strip()
        self._token_skipped = not tok
        return tok or None

    # --- steps
    def step_google(self, it: Item) -> None:
        from .google_setup import Wizard
        wz = (self.google_factory or Wizard)(
            self.home, self.cfg, ask=self._google_ask, say=self.io.out, open_url=self.open_url,
            clipboard=self.clipboard.write, **({"downloads": self.downloads} if self.downloads else {}))
        rc = wz.run()
        self._wizard_result("google", rc, fix=(f"מריצים שוב {COMMAND}", f"Run {COMMAND} again"))

    def _google_ask(self, prompt: str) -> str:
        a = self.raw_io.read(prompt)          # not _StepIO: here a bare `s` already means "skip this sub-step"
        if a is None:
            if getattr(self.io, "lines", None) is not None:
                return ""                    # scripted input: the end of the script means Enter (ConsoleIO's contract)
            raise EOFError                   # a real terminal closed: the wizard stops safely, progress is saved
        if is_skip_word(a, ("skip",)):
            raise StepSkipped                # the whole word abandons the Google step
        return a

    def step_telegram(self, it: Item) -> None:
        from . import telegram_channel as tg
        wz = tg.Wizard(self.home, self.io, transport=self.transport, polls=self.pair_polls,
                       poll_timeout=self.pair_poll_timeout, sleep=self.asleep, token_provider=self._token_provider,
                       auto_pair=True, on_link=self.open_url)
        had_token, paired_at = tg.token_present(), tg.pairing(self.home).get("paired_at")
        self._token_skipped = False
        try:
            rc = asyncio.run(wz.run())
        finally:
            self._finish_clip()
        if rc == 0 and not tg.pairing(self.home)["chats"]:
            rc = 1
        fix = (f"מריצים שוב {COMMAND} ולוחצים Start בבוט", f"Run {COMMAND} again and press Start in the bot")
        if rc not in (0, 130) and self._token_skipped:
            # the owner gave no token (declined, or no terminal): nothing was tried, so nothing failed
            self.results["telegram"] = "attention"
            self.notes["telegram"] = (f"מריצים שוב {COMMAND} כשהטוקן מוכן", f"Run {COMMAND} again when you have the bot token")
            return
        if rc not in (0, 130) and tg.token_present() and not had_token:
            # the token was accepted and saved this run; only the owner's Start tap is missing: not a failure
            self.results["telegram"], self.notes["telegram"] = "attention", fix
            return
        self._wizard_result("telegram", rc, fix=fix)
        if rc == 0 and tg.pairing(self.home).get("paired_at") != paired_at:
            self.h("נשלחה אליך הודעת אישור בטלגרם. מפעילים: jarvis features on telegram (כבוי כברירת מחדל).",
                   "A confirmation message was sent to your Telegram. Switch it on: jarvis features on telegram (off by default).")

    def step_pear(self, it: Item) -> None:
        url = self.cfg.music.pear_url
        if not self.pear_probe(url):
            self.h("Pear Desktop לא עונה. כך מפעילים את התוסף:\n" + PEAR_STEPS_HE,
                   "Pear Desktop does not answer. How to enable the plugin:\n" + PEAR_STEPS_EN)
            if not self.yes:
                self.h("מחכה ש-Pear יעלה (Ctrl+C לעצור)...", "Waiting for Pear to come up (Ctrl+C to stop)...")
                n = max(1, int(self.pear_wait_s / max(self.clip_poll_s, 0.001)))
                for _ in range(n):
                    if self.pear_probe(url):
                        break
                    asyncio.run(self.asleep(self.clip_poll_s))
            if not self.pear_probe(url):
                self.results["pear"] = "attention"
                self.notes["pear"] = (PEAR_STEPS_HE, PEAR_STEPS_EN)
                return
        from .skills.music import MusicError, PearDesktopPlayer
        self.h("Pear עונה. בחלון של Pear יופיע Allow: לוחצים עליו (פעם אחת).",
               "Pear answers. A dialog with Allow appears in Pear's window: click it (once).")
        p = PearDesktopPlayer(url, self.home / "pear_client_id", self.cfg.music.timeout_s, transport=self.pear_transport)

        async def pair() -> None:
            try:
                await p.pair(wait_s=self.pear_pair_wait_s)
            finally:
                await p.close()
        try:
            asyncio.run(pair())
        except MusicError as e:
            denied = e.code == "MUSIC_PAIR_DENIED"
            self.results["pear"] = "attention" if denied or e.code == "MUSIC_PLAYER_OFFLINE" else "failed"
            self.notes["pear"] = ("לחצו Allow בחלון של Pear ואז הריצו שוב " + COMMAND, f"Press Allow in Pear's window, then run {COMMAND} again")
            self.h(f"לא הושלם: {e.message}", f"Not finished: {e.message}")
            return
        self.results["pear"] = "connected"
        self.h("Pear מחובר. אפשר לומר: \"תנגן משהו רגוע\".", "Pear is paired. You can say: \"Play something calm\".")

    def step_wake(self, it: Item) -> None:
        if self.yes:
            self.results["wake"] = "skipped"
            self.notes["wake"] = (it.fix_he, it.fix_en)
            return
        rc = (self.wake_runner or _run_wake_test)()
        if rc == 0:
            self.results["wake"] = "connected"
        else:
            self.results["wake"] = "attention"
            self.notes["wake"] = ("בדקו את המיקרופון ואז: jarvis wake test --apply", "Check the microphone, then: jarvis wake test --apply")

    def _optional(self, fid: str, label: tuple[str, str], runner_name: str) -> None:
        if self.yes:
            self.results[fid] = "skipped"
            self.notes[fid] = (f"כשתרצו: jarvis {runner_name} start", f"When you want: jarvis {runner_name} start")
            return
        run = self.optional_runners.get(fid)
        if run is None:
            if fid == "instagram":
                from .instagram_channel import Wizard as W
            else:
                from .homeassistant import Wizard as W
            run = lambda: asyncio.run(W(self.home, self.io, transport=self.transport).run())      # noqa: E731
        self._wizard_result(fid, run(), fix=(f"jarvis {runner_name} start", f"jarvis {runner_name} start"))

    def step_instagram(self, it: Item) -> None:
        self._optional("instagram", ("אינסטגרם", "Instagram"), "instagram")

    def step_home(self, it: Item) -> None:
        self._optional("home", ("Home Assistant", "Home Assistant"), "home")

    def _wizard_result(self, fid: str, rc: int, fix: tuple[str, str]) -> None:
        if rc == 0:
            self.results[fid] = "connected"
        elif rc == 130:
            raise KeyboardInterrupt
        else:
            self.results[fid] = "failed"
            self.notes[fid] = fix
            logs.warn("connect", "CONNECT_STEP_FAILED", "setup step did not finish", step=fid, rc=rc)


def _run_wake_test() -> int:
    return subprocess.run([sys.executable, "-m", "jarves", "wake", "test", "--apply"]).returncode


# ---------------------------------------------------------------------- the once-a-day reminder
def maybe_nudge(db, alerts, *, home: Path, features=None, clock: Callable[[], float] = time.time,
                channel: str = "speech") -> dict | None:
    """Remind the owner (voice when a conversation is open, else a quiet popup) that Google is not connected.
    At most once per 24 h; never in quiet hours or while alerts are paused; not when proactive alerts are off, when
    the autonomy level is observe-only, when the guided_setup feature is off, or after `jarvis connect --reminders off`."""
    from .google_auth import GoogleAuth
    if features is not None and not features.active(FEATURE):
        return None
    if not db.setting("connect.nudge") or db.setting("autonomy.level") == "observe_only":
        return None
    if "google" in (db.setting("connect.skipped") or []):          # "never ask again" (jarvis connect --reset-skips)
        return None
    try:
        if not alerts.enabled():
            return None
    except Exception:
        return None
    if alerts.quiet_now() or alerts.paused():
        return None
    now = clock()
    if now - float(db.setting("connect.last_nudge") or 0) < NUDGE_EVERY_S:
        return None
    g = GoogleAuth(Path(home)).status()
    if g["connected"]:
        return None
    db.set_setting("connect.last_nudge", now, actor="connect")
    text = NUDGE_TEXT_RECONNECT_HE if g["needs_reconnect"] else NUDGE_TEXT_HE
    return alerts.raise_("connect", "חיבור גוגל", text, priority="normal", kind="suggestion", channel=channel,
                         dedupe_key="connect.google")


# ---------------------------------------------------------------------- CLI
def add_cli(sub) -> None:
    c = sub.add_parser("connect", help="ONE guided command: checks Google, Telegram, the Pear plugin and the wake "
                                       "word and walks you through only what is missing (opens the pages, watches "
                                       "the clipboard and Downloads, pairs by itself)")
    c.add_argument("--only", choices=list(ONLY), help="just this one")
    c.add_argument("--status", action="store_true", help="show what is connected; change nothing")
    c.add_argument("--yes", action="store_true", help="accept the default answers and skip the optional accounts")
    c.add_argument("--json", action="store_true", help="with --status: machine-readable output")
    c.add_argument("--reminders", choices=["on", "off"], help="the once-a-day 'Google is not connected' reminder")
    c.add_argument("--skip", help="comma-separated items to skip in this run, e.g. --skip telegram,instagram "
                                  "(items: " + ", ".join(ONLY) + ")")
    c.add_argument("--reset-skips", action="store_true", help="forget every 'never ask again' choice")


def cmd_connect(a, cfg, home) -> int:
    import json

    from .userdata import open_db
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    if getattr(a, "reminders", None):
        db = open_db(home)
        try:
            db.set_setting("connect.nudge", a.reminders == "on", actor="cli")
        finally:
            db.close()
        clilang.say(f"התזכורת היומית: {'דולקת' if a.reminders == 'on' else 'כבויה'}.", f"Reminders {a.reminders}.")
        return 0
    skip = [x.strip().lower() for x in (getattr(a, "skip", None) or "").split(",") if x.strip()]
    bad = [x for x in skip if x not in ONLY]
    if bad:
        print(f"Unknown item(s) for --skip: {', '.join(bad)}. Items: {', '.join(ONLY)}", file=sys.stderr)
        return 2
    if getattr(a, "json", False) and getattr(a, "status", False):
        print(json.dumps(items_json(home, cfg), ensure_ascii=False, indent=1))
        return 0
    return Connector(home, cfg, yes=bool(getattr(a, "yes", False)), only=getattr(a, "only", None),
                     status_only=bool(getattr(a, "status", False)), skip=skip,
                     reset_skips=bool(getattr(a, "reset_skips", False))).run()


# ---------------------------------------------------------------------- feature + dashboard
def _launch_console() -> bool:
    """Open `jarvis connect` in its own console window (it needs the keyboard). Windows only; elsewhere False."""
    if sys.platform != "win32":
        return False
    subprocess.Popen([sys.executable, "-m", "jarves", "connect"], creationflags=subprocess.CREATE_NEW_CONSOLE)
    return True


LAUNCH: Callable[[], bool] = _launch_console

feat.register(FEATURE, "חיבור חשבונות מודרך", "Guided account setup", True,
              about="one command, jarvis connect, checks every account (Google, Telegram, Pear, wake word) and walks "
                    "you through only what is missing; reminds you once a day while Google is not connected",
              targets=targets("UNIT test_connect.py (real HTTP fake Telegram with getUpdates pairing, fake Pear, fake "
                              "Google, fake clipboard, tmp Downloads)",
                              "never run against the owner's real Telegram, Google or Pear accounts"),
              permissions=["read", "action (opens pages, clipboard watch with consent)"],
              settings=COMMAND + ", dashboard Connections page, `jarvis connect --reminders off`")


def dashboard_routes(register) -> None:
    def get(ctx, _m, _b):
        return 200, items_json(ctx.home, ctx.cfg)

    def run(ctx, _m, _b):
        ok = bool(LAUNCH())
        return 200, {"started": ok, "command": COMMAND,
                     "message_he": "נפתח חלון עם " + COMMAND + "." if ok else f"פותחים טרמינל ומריצים: {COMMAND}",
                     "message_en": f"A window with {COMMAND} opened." if ok else f"Open a terminal and run: {COMMAND}"}
    register("GET", r"/api/connect", get)
    register("POST", r"/api/connect/run", run)
