"""Read-only connectivity checks for `jarvis connect --check` and the dashboard Connections page.

A saved credential is not proof that an integration works. For every integration marked connected this module runs
ONE minimal harmless probe and answers: working / broken (short reason + the exact fix command) / unreachable /
not tested. A probe never sends a message, never writes owner data, never opens a browser and never spends money.
Probes run concurrently in daemon threads with a short timeout; results are cached for a minute so the dashboard and
repeated runs stay cheap. Network access is injectable per integration (`CheckCtx.transports`) so tests use fakes.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from . import logs

WORKING, BROKEN, UNREACHABLE, NOT_TESTED = "working", "broken", "unreachable", "not_tested"
STATES = (WORKING, BROKEN, UNREACHABLE, NOT_TESTED)
WORDS = {WORKING: ("עובד", "working"), BROKEN: ("שבור", "broken"), UNREACHABLE: ("לא זמין", "unreachable"),
         NOT_TESTED: ("לא נבדק", "not tested")}
TTL_S = 60.0
TIMEOUT_S = 8.0
OPENAI_API = "https://api.openai.com/v1"


@dataclass
class CheckResult:
    state: str = NOT_TESTED
    detail: str = ""
    fix: str = ""                    # the exact command that repairs it
    at: float = field(default_factory=time.time)
    detail_he: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckCtx:
    home: Path
    cfg: Any
    transports: dict = field(default_factory=dict)          # id -> httpx transport (tests); None = the real network
    pear_probe: Callable[[str], bool] | None = None
    wake_loader: Callable[[Any], Any] | None = None

    def transport(self, key: str):
        return self.transports.get(key)


HE = {"key accepted": "המפתח התקבל", "no API key": "אין מפתח", "OpenAI could not be reached": "אי אפשר להגיע ל-OpenAI",
      "OpenAI rejected the key": "OpenAI דחה את המפתח", "no admin key": "אין מפתח אדמין", "linked and connected": "מחובר",
      "admin key accepted (read-only costs)": "מפתח האדמין התקבל (קריאה בלבד)", "OpenAI rejected the admin key": "OpenAI דחה את מפתח האדמין",
      "signed out": "לא מחובר", "plan usage was not granted": "לא ניתנה הרשאה לשימוש בתוכנית", "token valid": "האסימון תקף",
      "token refreshed": "האסימון חודש", "token expired and could not be refreshed": "האסימון פג ולא הצלחתי לחדש אותו",
      "OpenAI sign-in could not be reached": "אי אפשר להגיע להתחברות של OpenAI", "calendar and mail answer": "היומן והמייל עונים",
      "the Google connection expired": "החיבור לגוגל פג", "not signed in": "לא מחובר", "Google could not be reached": "אי אפשר להגיע לגוגל",
      "Pear Desktop does not answer": "Pear Desktop לא עונה", "Pear answers but is not paired": "Pear עונה אבל לא מחובר",
      "Pear answers and is paired": "Pear עונה ומחובר", "wake-word model loads": "מודל מילת ההפעלה נטען",
      "no bot token": "אין טוקן לבוט", "no paired chat": "אין צ'אט מחובר", "no access token": "אין טוקן גישה",
      "not set up": "לא הוגדר", "Home Assistant answers": "Home Assistant עונה", "tailscale up": "Tailscale פועל"}


def _he(r: CheckResult) -> str:
    """A short Hebrew line for the dashboard: the known fixed details, else the plain state."""
    d = r.detail
    if d in HE:
        return HE[d]
    if d.startswith("bot @") and d.endswith(" answers"):
        return "הבוט " + d[4:-8] + " עונה"
    if d.endswith(" answers") and d.startswith("@"):
        return d[:-8] + " עונה"
    if d.startswith("the wake-word model did not load"):
        return "מודל מילת ההפעלה לא נטען"
    if d.startswith("no answer within"):
        return "אין תשובה בזמן"
    if not d:
        return ""
    return {WORKING: "עובד", BROKEN: "יש תקלה", UNREACHABLE: "אין תשובה", NOT_TESTED: "לא נבדק"}[r.state]


Check = Callable[[CheckCtx], CheckResult]
CHECKS: dict[str, Check] = {}
_CACHE: dict[tuple[str, str], tuple[float, CheckResult]] = {}
_LOCK = threading.Lock()
clock: Callable[[], float] = time.monotonic              # tests may replace it


def register_check(fid: str, fn: Check) -> None:
    CHECKS[fid] = fn


def _ok(detail: str = "") -> CheckResult:
    return CheckResult(WORKING, detail)


def _broken(detail: str, fix: str) -> CheckResult:
    return CheckResult(BROKEN, detail, fix)


def _down(detail: str, fix: str = "") -> CheckResult:
    return CheckResult(UNREACHABLE, detail, fix)


# ---------------------------------------------------------------------- the probes
def _http_get(url: str, headers: dict, transport, params: dict | None = None) -> httpx.Response:
    with httpx.Client(timeout=TIMEOUT_S - 1, transport=transport) as c:
        return c.get(url, headers=headers, params=params)


def check_openai(c: CheckCtx) -> CheckResult:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return _broken("no API key", "jarvis key")
    try:      # listing models is free and read-only
        r = _http_get(os.environ.get("JARVES_OPENAI_API_BASE", OPENAI_API).rstrip("/") + "/models",
                      {"Authorization": f"Bearer {key}"}, c.transport("openai"))
    except httpx.HTTPError:
        return _down("OpenAI could not be reached")
    if r.status_code == 200:
        return _ok("key accepted")
    if r.status_code in (401, 403):
        return _broken("OpenAI rejected the key", "jarvis key")
    return _down(f"OpenAI answered {r.status_code}")


def check_openai_admin(c: CheckCtx) -> CheckResult:
    key = os.environ.get("OPENAI_ADMIN_KEY", "").strip()
    if not key:
        from .usage import ADMIN_ENV, admin_key_saved
        if admin_key_saved(c.home):                       # saved in secrets.env but not loaded into this process
            from .config import secrets_file
            for line in secrets_file(c.home).read_text(encoding="utf-8-sig").splitlines():
                name, _, val = line.partition("=")
                if name.strip() == ADMIN_ENV:
                    key = val.strip().strip("\"'")
    if not key:
        return _broken("no admin key", "jarvis connect openai-admin")
    try:
        r = _http_get(os.environ.get("JARVES_OPENAI_API_BASE", OPENAI_API).rstrip("/") + "/organization/costs",
                      {"Authorization": f"Bearer {key}"}, c.transport("openai-admin"),
                      {"start_time": int(time.time()) - 86400, "limit": 1})
    except httpx.HTTPError:
        return _down("OpenAI could not be reached")
    if r.status_code == 200:
        return _ok("admin key accepted (read-only costs)")
    if r.status_code in (401, 403):
        return _broken("OpenAI rejected the admin key", "jarvis connect openai-admin")
    return _down(f"OpenAI answered {r.status_code}")


def check_chatgpt(c: CheckCtx) -> CheckResult:
    from .chatgpt_auth import ChatGPTAuth, ChatGPTAuthError
    auth = ChatGPTAuth(c.home, transport=c.transport("chatgpt"))
    st = auth.status()
    fix = "jarvis chatgpt login"
    if not st.get("signed_in"):
        return _broken("signed out", fix)
    if not st.get("plan_usage"):
        return _broken("plan usage was not granted", fix)
    if st.get("access_expires_in_s", 0) > 120:
        return _ok("token valid")                       # nothing refreshed: the owner's token is left alone
    try:                                                # expired: only now the normal refresh runs
        asyncio.run(auth.access_token())
    except ChatGPTAuthError:
        return _broken("token expired and could not be refreshed", fix)
    except httpx.HTTPError:
        return _down("OpenAI sign-in could not be reached")
    return _ok("token refreshed")


def check_google(c: CheckCtx) -> CheckResult:
    from .google_auth import GoogleAuth
    from .google_setup import self_check
    auth = GoogleAuth(c.home, transport=c.transport("google"))
    st = auth.status()
    if not st["connected"]:
        return _broken("the Google connection expired" if st["needs_reconnect"] else "not signed in", "jarvis connect google")
    r = asyncio.run(self_check(auth, gmail=True, transport=c.transport("google")))
    if r.get("ok"):
        return _ok("calendar and mail answer")
    err = str(r.get("error", ""))
    if err == "GOOGLE_OFFLINE":
        return _down("Google could not be reached")
    return _broken(err or "check failed", "jarvis connect google")


def check_pear(c: CheckCtx) -> CheckResult:
    from .connect import pear_up
    url = getattr(getattr(c.cfg, "music", None), "pear_url", "http://127.0.0.1:26538")
    if not (c.pear_probe or pear_up)(url):
        return _down("Pear Desktop does not answer", "jarvis music open")
    if not (c.home / "pear_client_id.paired").exists():
        return _broken("Pear answers but is not paired", "jarvis connect pear")
    return _ok("Pear answers and is paired")


def check_wake(c: CheckCtx) -> CheckResult:
    try:
        if c.wake_loader:
            c.wake_loader(c.cfg.voice)
        else:
            from .voice.wakeword import make_wake_detector
            make_wake_detector(c.cfg.voice)
    except Exception as e:                              # a missing model / package: broken, with the repair command
        return _broken(f"the wake-word model did not load ({type(e).__name__})", "jarvis wake test --apply")
    return _ok("wake-word model loads")


def check_telegram(c: CheckCtx) -> CheckResult:
    from . import telegram_channel as tg
    fix = "jarvis telegram start"
    if not tg.token_present():
        return _broken("no bot token", fix)
    if not tg.pairing(c.home)["chats"]:
        return _broken("no paired chat", fix)
    try:
        me = asyncio.run(tg.TelegramApi(transport=c.transport("telegram"), timeout=TIMEOUT_S - 1).get_me())    # getMe: read-only
    except tg.TelegramError as e:
        return _down(e.message) if e.code in ("TELEGRAM_UNREACHABLE", "TELEGRAM_RATE_LIMIT") else _broken(e.message, fix)
    return _ok("bot @" + str(me.get("username", "?")) + " answers")


def check_instagram(c: CheckCtx) -> CheckResult:
    from . import instagram_channel as ig
    fix = "jarvis instagram start"
    if not ig.token_present():
        return _broken("no access token", fix)
    try:
        me = asyncio.run(ig.InstagramApi(transport=c.transport("instagram"), timeout=TIMEOUT_S - 1).me())
    except ig.InstagramError as e:
        return _down(e.message) if e.code in ("INSTAGRAM_UNREACHABLE", "INSTAGRAM_RATE_LIMIT") else _broken(e.message, fix)
    return _ok("@" + str(me.get("username", "?")) + " answers")


def check_home(c: CheckCtx) -> CheckResult:
    from . import homeassistant as ha
    fix = "jarvis home start"
    if not ha.configured(c.home):
        return _broken("not set up", fix)
    try:
        asyncio.run(ha.HAClient(ha.load_config(c.home)["url"], os.environ.get(ha.TOKEN_ENV, "").strip(),
                                c.transport("home")).check())
    except ha.HAError as e:
        return _down(e.message) if "UNREACHABLE" in e.code or "OFFLINE" in e.code else _broken(e.message, fix)
    return _ok("Home Assistant answers")


for _id, _fn in (("openai", check_openai), ("openai-admin", check_openai_admin), ("chatgpt", check_chatgpt),
                 ("google", check_google), ("pear", check_pear), ("wake", check_wake), ("telegram", check_telegram),
                 ("instagram", check_instagram), ("home", check_home)):
    register_check(_id, _fn)


# ---------------------------------------------------------------------- running them
def _run_one(fid: str, ctx: CheckCtx) -> CheckResult:
    fn = CHECKS.get(fid)
    if fn is None:
        return CheckResult(NOT_TESTED, "no check for this integration yet", detail_he="אין עדיין בדיקה לחיבור הזה")
    try:
        r = fn(ctx)
        r.detail_he = r.detail_he or _he(r)
        return r
    except Exception as e:                              # a probe bug must never break the command
        logs.warn("connect", "CONNECT_CHECK_ERROR", "a connectivity check raised an error", step=fid, error=type(e).__name__)
        return CheckResult(UNREACHABLE, f"the check did not finish ({type(e).__name__})", detail_he="הבדיקה לא הסתיימה")


def cached(home: Path, fid: str) -> CheckResult | None:
    with _LOCK:
        hit = _CACHE.get((str(home), fid))
    return hit[1] if hit and clock() - hit[0] < TTL_S else None


def run_checks(ctx: CheckCtx, ids: list[str], *, fresh: bool = False, timeout: float = TIMEOUT_S) -> dict[str, CheckResult]:
    """Check `ids` concurrently (daemon threads, `timeout` seconds each). Never raises."""
    out: dict[str, CheckResult] = {}
    todo: list[str] = []
    for fid in ids:
        hit = None if fresh else cached(ctx.home, fid)
        if hit is not None:
            out[fid] = hit
        else:
            todo.append(fid)
    slots: dict[str, CheckResult] = {}

    def work(fid: str) -> None:
        slots[fid] = _run_one(fid, ctx)
    threads = {fid: threading.Thread(target=work, args=(fid,), daemon=True, name="connect-check-" + fid) for fid in todo}
    for t in threads.values():
        t.start()
    end = time.monotonic() + timeout
    for fid, t in threads.items():
        t.join(max(0.0, end - time.monotonic()))
        res = slots.get(fid) or CheckResult(UNREACHABLE, f"no answer within {int(timeout)} s")
        res.detail_he = res.detail_he or _he(res)
        out[fid] = res
        with _LOCK:
            _CACHE[(str(ctx.home), fid)] = (clock(), res)
    return {fid: out[fid] for fid in ids}


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
