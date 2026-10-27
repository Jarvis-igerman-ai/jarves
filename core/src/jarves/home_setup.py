"""Home setup cards (#457, V5): one guided card per still-open setup item on the dashboard Home.

Beta, OFF by default. Reuses the `jarvis connect` checklist (status, the read-only connectivity check, the "never ask
again" list) and adds only what the cards need: the owner's answer per card (Works / Did not work / Later) is remembered,
so a card the owner already dealt with does not come back. Cards are shown for Telegram, Pear Desktop, the wake word and
Google only, and only while the item is not connected, not skipped and not snoozed. No secret is ever asked for here:
a card holds steps and a button to the existing connect flow; tokens are entered in that flow (hidden input).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import connect as cn
from . import features as feat
from .calm import targets

FEATURE = "home_setup_cards"
SETTING = "home_setup.state"
CARD_IDS = ("google", "telegram", "pear", "wake")
LATER_S = 3 * 24 * 3600                                  # "Later" hides a card for three days
ANSWERS = ("works", "failed", "later")
GUIDE = {"google": "google_calendar", "telegram": "telegram", "pear": "music", "wake": "wake_word"}
STEPS: dict[str, tuple[str, str]] = {
    "google": ("1. לוחצים על 'חיבור' ובוחרים את חשבון גוגל. 2. מסמנים את כל ההרשאות ולוחצים Continue. 3. חוזרים לכאן ולוחצים 'עובד'.",
               "1. Press Connect and pick your Google account. 2. Tick every permission and press Continue. 3. Come back here and press Works."),
    "telegram": ("1. בטלגרם פותחים את BotFather ושולחים /newbot. 2. בוחרים שם ושם משתמש לבוט. 3. מעתיקים את הטוקן שהוא נותן (לא שולחים אותו לאף אחד, מדביקים רק בחלון החיבור). 4. לוחצים Start בבוט החדש.",
 "1. In Telegram open BotFather and send /newbot. 2. Pick a name and a username for the bot. 3. Copy the token it gives you (share it with nobody; paste it only in the connect window). 4. Press Start in your new bot."),
    "pear": (cn.PEAR_STEPS_HE, cn.PEAR_STEPS_EN),
    "wake": ("1. יושבים ליד המיקרופון בחדר שקט. 2. לוחצים 'חיבור'. 3. אומרים Hey Jarvis כמה פעמים (כ-20 שניות).",
             "1. Sit near the microphone in a quiet room. 2. Press Connect. 3. Say Hey Jarvis a few times (about 20 seconds)."),
}


def _db(home: Path):
    from .userdata import open_db
    return open_db(home)


def _state(home: Path) -> dict:
    if not (Path(home) / "memory.db").exists():
        return {}
    db = _db(home)
    try:
        v = db.setting(SETTING)
    finally:
        db.close()
    return {k: dict(x) for k, x in (v if isinstance(v, dict) else {}).items() if k in CARD_IDS and isinstance(x, dict)}


def _save(home: Path, st: dict) -> None:
    db = _db(home)
    try:
        db.set_setting(SETTING, st, actor="dashboard")
    finally:
        db.close()


def cards(home: Path, cfg=None, *, now: float, items: list | None = None) -> list[dict]:
    """The open cards, in CARD_IDS order. `items` = connect.status_items (injectable for tests)."""
    home = Path(home)
    items = items if items is not None else cn.status_items(home, cfg)
    never = cn.never_list(home)
    st = _state(home)
    out = []
    for it in items:
        if it.id not in CARD_IDS or it.state == "connected" or it.id in never:
            continue
        s = st.get(it.id, {})
        if float(s.get("later_until", 0) or 0) > now:
            continue
        he, en = STEPS[it.id]
        out.append({"id": it.id, "title_he": it.title_he, "title_en": it.title_en, "detail_he": it.detail_he,
                    "detail_en": it.detail_en, "steps_he": he, "steps_en": en, "guide": GUIDE[it.id],
                    "failed": int(s.get("failed", 0) or 0)})
    out.sort(key=lambda c: CARD_IDS.index(c["id"]))
    return out


def answer(home: Path, cfg, fid: str, ans: str, *, now: float, check: Callable[[str], bool] | None = None) -> dict:
    """Record the owner's answer. works: confirm with the read-only check (a card that does not really work stays and
    counts as 'did not work'); failed: counted, the card keeps its steps and shows the guide; later: hidden 3 days."""
    if fid not in CARD_IDS or ans not in ANSWERS:
        raise ValueError("bad card or answer")
    st = _state(home)
    s = st.setdefault(fid, {})
    result = {"id": fid, "answer": ans, "hidden": False}
    if ans == "later":
        s["later_until"] = now + LATER_S
        result["hidden"] = True
    elif ans == "failed":
        s["failed"] = int(s.get("failed", 0) or 0) + 1
    else:
        ok = check(fid) if check else _really_connected(home, cfg, fid)
        if ok:
            s.clear()
            s["done"] = now
            result["hidden"] = True
        else:
            s["failed"] = int(s.get("failed", 0) or 0) + 1
            result["answer"] = "failed"
            result["message_he"] = "עוד לא מחובר. עברו על השלבים או פתחו את המדריך."
            result["message_en"] = "Not connected yet. Go through the steps or open the guide."
    _save(home, st)
    return result


def _really_connected(home: Path, cfg, fid: str) -> bool:
    return any(i.id == fid and i.state == "connected" for i in cn.status_items(home, cfg))


def dashboard_routes(register) -> None:
    def _on(ctx) -> bool:
        try:
            return feat.Features(ctx.cfg, ctx.home).enabled(FEATURE)
        except Exception:
            return False

    def get(ctx, _m, _b):
        import time
        if not _on(ctx):
            return 200, {"enabled": False, "cards": []}
        return 200, {"enabled": True, "cards": cards(ctx.home, ctx.cfg, now=time.time())}

    def post(ctx, _m, body):
        import time
        if not _on(ctx):
            return 403, {"error": "home setup cards are off (beta): jarvis features on " + FEATURE}
        try:
            return 200, answer(ctx.home, ctx.cfg, str((body or {}).get("id") or ""), str((body or {}).get("answer") or ""),
                               now=time.time())
        except ValueError as e:
            return 400, {"error": str(e)}
    register("GET", r"/api/home/setup-cards", get)
    register("POST", r"/api/home/setup-cards", post)


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    return True, ""


feat.register(FEATURE, "כרטיסי הגדרה בדף הבית", "Home setup cards", False, probe=_probe,
              risk="low",
              about="show one guided card on the dashboard Home for each still-open setup item (Google, Telegram, Pear, "
                    "wake word) with Works / Did not work / Later; it remembers your answers and never asks for secrets",
              targets=targets("test_home_setup.py"),
              permissions=["read"], settings="jarvis features on home_setup_cards")
