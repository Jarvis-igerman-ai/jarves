"""`jarvis whatsapp ...` and the dashboard WhatsApp page (SW-V4 F, Issue #17): the PC-only side of the bridge.

Everything that changes what JARVES may do on WhatsApp lives here and needs the bridge's ADMIN token: mode
(off / read / send), the read and send allow-lists, the read scope, kill + release, link / unlink, retention and
purge. Only two callers exist: this CLI (the owner at the PC) and the dashboard's LOCAL session (a paired phone gets
403 here, and the dashboard's own allow-list refuses its writes anyway). No model tool and no voice path can reach
these functions (tests/test_whatsapp_policy.py checks the registry).

Sending from the CLI goes through the same approvals popup as voice: recipient, number and the full text.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from . import clilang, logs
from . import whatsapp_bridge as wab
from . import whatsapp_bridge_install as wbi
from . import whatsapp_policy as pol
from .whatsapp_bridge import BridgeClient, BridgeError

LINK_WAIT_S = 180.0
TOS_NOTE = ("Unofficial linked-device client: this breaks WhatsApp's Terms and carries a real, unquantified risk "
            "of a temporary or permanent ban of the number. You accepted it for your main number, read-only first.")
TOS_NOTE_HE = ("זה לקוח לא רשמי שמתחבר כמכשיר מקושר: זה מפר את תנאי השימוש של וואטסאפ ויש סיכון אמיתי (לא ידוע "
               "בכמה) לחסימה זמנית או קבועה של המספר. אישרת את זה למספר הראשי, קודם לקריאה בלבד.")


def admin(home: Path) -> BridgeClient:
    return BridgeClient(home, admin=True)


def kill_file(home: Path) -> Path:
    return wab.bridge_dir(home) / "KILL"


def kill(home: Path, *, logout: bool = False, by: str = "cli") -> dict:
    """Works even when the bridge is down or hung: the KILL file is what the bridge obeys (polled every 250 ms)."""
    d = wab.bridge_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    kill_file(home).write_text(f"owner_{by}\n", encoding="utf-8")
    out = {"killed": True, "file": True, "bridge": False}
    try:
        admin(home).call("POST", "/kill", body={"on": True, "logout": bool(logout)})
        out["bridge"] = True
    except BridgeError as e:
        out["bridge_error"] = e.code
    logs.warn("whatsapp", "WA_KILLED_BY_OWNER", "WhatsApp kill switch pressed", by=by, logout=bool(logout))
    return out


def release(home: Path, *, by: str = "cli") -> dict:
    try:
        r = admin(home).call("POST", "/kill", body={"on": False})
    except BridgeError:
        kill_file(home).unlink(missing_ok=True)
        r = {"killed": False}
    kill_file(home).unlink(missing_ok=True)
    logs.info("whatsapp", "WA_KILL_RELEASED", "WhatsApp kill switch released", by=by)
    return r


def set_mode(home: Path, mode: str, *, override_watch: bool = False, by: str = "cli") -> dict:
    if mode not in pol.MODES:
        raise BridgeError("WA_BAD_REQUEST", "mode must be off, read or send", 400)
    r = admin(home).call("POST", "/mode", body={"mode": mode, "override_watch": bool(override_watch)})
    logs.info("whatsapp", "WA_MODE_CHANGED", "WhatsApp bridge mode changed on the PC", mode=mode, by=by,
              override=bool(override_watch))
    return r


READ_ON_EN = "Read-only mode and the WhatsApp reading switch are on. JARVES reads, it never sends."
READ_ON_HE = "מצב קריאה בלבד ומתג הקריאה של וואטסאפ פועלים. ג'ארביס קורא ואף פעם לא שולח."
SWITCH_CMD = f"jarvis features on {wab.FEATURE}"
SWITCH_OFF_EN = f"reading switch: off. JARVES cannot read your WhatsApp until you turn it on: {SWITCH_CMD}"
SWITCH_OFF_HE = f"מתג הקריאה כבוי. ג'ארביס לא יכול לקרוא את הוואטסאפ עד שתדליקו אותו: {SWITCH_CMD}"
READ_LATER_EN = f"Mode is read, but the reading switch stays off. To turn it on later: {SWITCH_CMD}"
READ_LATER_HE = f"המצב הוא קריאה, אבל מתג הקריאה נשאר כבוי. להדלקה אחר כך: {SWITCH_CMD}"


def _features(home: Path, cfg=None):
    """The same Features object `jarvis features on ...` uses (writes features.json; a running Core picks it up)."""
    from .cli import _cli_features
    if cfg is None:
        from .config import load_config
        cfg = load_config()[0]
    return _cli_features(cfg, home)


def reading_switch_on(home: Path, features=None) -> bool:
    try:
        return bool((features or _features(home)).enabled(wab.FEATURE))
    except Exception:
        return False


def enable_reading(home: Path, *, by: str, features=None, cfg=None) -> dict:
    """The ONE 'yes, switch on read-only' backend (dashboard page, Connect row, `jarvis whatsapp setup`): bridge mode
    read AND the `whatsapp_bridge` feature switch, through the same `switch()` as `jarvis features on`. Only called
    after the owner's explicit yes. Never touches `whatsapp_send`; sending stays off."""
    from .features import switch
    r = set_mode(home, "read", by=by)
    s = switch(features or _features(home, cfg), wab.FEATURE, True, by=by)
    logs.info("whatsapp", "WA_READING_SWITCH_ON", "WhatsApp reading switch turned on with read-only mode", by=by)
    return {**r, "mode": r.get("mode", "read"), "feature": wab.FEATURE, "feature_on": bool(s.get("enabled")),
            "message": READ_ON_EN, "message_he": READ_ON_HE}


def resolve_contact(home: Path, who: str) -> tuple[str | None, str]:
    """(JID, label) for a name or number the owner typed; a number never needs an existing chat for READ lists."""
    if any(ch.isdigit() for ch in who) and pol.number_to_jid(who):
        return pol.number_to_jid(who), who
    try:
        chats = BridgeClient(home).chats(None, 500)
    except BridgeError:
        chats = []
    hits = wab.resolve(chats, who, wab.contacts_book(home))
    if len(hits) == 1:
        return hits[0]["id"], hits[0].get("name") or who
    return None, ("several chats match" if hits else "no chat matches")


def allow(home: Path, lst: str, op: str, who: str, *, by: str = "cli") -> dict:
    jid, label = resolve_contact(home, who)
    if jid is None:
        raise BridgeError("WA_UNKNOWN_CONTACT", label, 404)
    r = admin(home).call("POST", "/allow", body={"list": lst, "op": op, "chat": jid})
    logs.info("whatsapp", "WA_ALLOW_CHANGED", "WhatsApp allow-list changed on the PC", list=lst, op=op,
              chat_hash=pol.chat_hash(jid), by=by)
    return {**r, "label": label}


async def _ask_send(home: Path, to: str, name: str, number: str, text: str, *, test_invoke: str | None = None) -> bool:
    """The CLI's send approval: the same popup (recipient, number, FULL text; typed code at night)."""
    from . import approvals
    svc = approvals.ApprovalService(home)
    svc.channels.append(approvals.PopupChannel(svc, enabled=True, test_invoke=test_invoke))
    night = pol.quiet(pol.local_hour())
    grant = wab.grant_for(to, text, "cli")
    det = {"recipient": name, "number": number or "(LID contact)", "text": text, "grant": grant}
    if night:
        det["warning"] = "23:00-07:00: type the code to approve"
    ok = await svc.ask(f"Send on WhatsApp to {name} ({number}):\n{text}", title="whatsapp_send", kind="message",
                       risk="high", details=det, typed=night, source="user", voice=False,
                       why="you ran `jarvis whatsapp send` on this computer")
    return ok and svc.take_grant(grant)


def send(home: Path, who: str, text: str, *, test_invoke: str | None = None) -> dict:
    text = text.strip()
    user = BridgeClient(home)
    st = user.status()
    if st.get("killed"):
        raise BridgeError("WA_KILLED", "", 423)
    if st.get("mode") != "send":
        raise BridgeError("WA_MODE_READ" if st.get("mode") == "read" else "WA_MODE_OFF", "", 409)
    hits = wab.resolve(user.chats(None, 500), who, wab.contacts_book(home))
    if len(hits) != 1:
        raise BridgeError("WA_AMBIGUOUS" if hits else "WA_UNKNOWN_CONTACT", "", 404)
    c = hits[0]
    if not asyncio.run(_ask_send(home, c["id"], c.get("name") or "?", c.get("number") or "", text,
                                 test_invoke=test_invoke)):
        raise BridgeError("WA_APPROVAL_MISSING", "not approved on the screen", 403)
    token = wab.mint_approval(home, c["id"], text, night=pol.quiet(pol.local_hour()))
    if token is None:
        raise BridgeError("WA_NOT_SET_UP", "", 409)
    r = user.send(c["id"], text, token)
    wab.note_sent(home, c["id"])
    logs.info("whatsapp", "WA_SENT", "WhatsApp message sent after the owner's approval",
              chat_hash=pol.chat_hash(c["id"]), chars=len(text), via="cli")
    return r


# ---------------------------------------------------------------------- CLI
ACTIONS = ("status", "start", "stop", "install", "setup", "link", "unlink", "mode", "allow", "remove", "scope", "inbox", "send", "kill",
           "purge", "retention", "audit")


def add_cli(sub) -> None:
    w = sub.add_parser("whatsapp", help="real WhatsApp access through the local bridge (PC only): status | start | "
                                        "stop | install | setup | link | unlink | mode off|read|send | allow read|send <contact> | "
                                        "remove read|send <contact> | scope | inbox | send <contact> \"<text>\" | "
                                        "kill | purge | retention <days> | audit")
    w.add_argument("action", choices=ACTIONS)
    w.add_argument("args", nargs="*", help="mode: off|read|send; allow/remove: read|send <contact>; send: <contact> "
                                          "<text>; retention: <days>; inbox: [contact]")
    w.add_argument("--phone", default="", help="link: get an 8-character pairing code for this number instead of a QR")
    w.add_argument("--window", action="store_true", help="link: also show the QR on the dashboard WhatsApp page")
    w.add_argument("--override-watch", action="store_true",
                   help="mode send: skip the 14-day read-only watch (asks you to type SEND)")
    w.add_argument("--release", action="store_true", help="kill: release the kill switch")
    w.add_argument("--logout", action="store_true", help="kill: also unlink the device on WhatsApp's side")
    w.add_argument("--older-than", type=int, default=None, help="purge: only messages older than N days")
    w.add_argument("--keep-nothing", choices=["on", "off"], default=None, help="retention: live only, store nothing")
    w.add_argument("--groups", choices=["on", "off"], default=None, help="scope: show groups on the read list")
    w.add_argument("--all-direct", choices=["on", "off"], default=None, help="scope: read every 1:1 chat")
    w.add_argument("--limit", type=int, default=10)
    w.add_argument("--json", action="store_true")
    w.add_argument("--yes", action="store_true", help="unlink/purge: do not ask again")


def _print(d, as_json: bool) -> None:
    print(json.dumps(d, ensure_ascii=False, indent=None if as_json else 1))


def _confirm(prompt: str, want: str = "y") -> bool:
    try:
        ans = input(prompt)
    except (EOFError, OSError):
        return False
    return ans.strip().lower() == want.lower() if want != "y" else ans.strip().lower() in ("y", "yes", "כן")


def _down_code(home: Path) -> str:
    return "WA_BRIDGE_DOWN" if wab.bridge_installed(home) else "WA_NOT_INSTALLED"


def install_bridge(home: Path) -> dict:
    """The ONE 'Download the WhatsApp bridge' backend (dashboard WhatsApp page, CLI `jarvis whatsapp install`, the
    Connect flow). Owner-initiated only (callers are local-only); a signed manifest + SHA256 + size or nothing."""
    try:
        return wbi.install(home, approved=True)
    except wbi.BridgeInstallError as e:
        return wbi.failure(e)


def setup_begin(home: Path, phone: str = "") -> dict:
    """The ONE 'Set up WhatsApp' backend (dashboard WhatsApp page, dashboard Connect row; the CLI/Connect console use
    the same start + link calls). Starts the bridge if needed and begins linking. It never scans for you: the phone
    scan stays manual. `step`: not_installed | bridge_down | already_linked | awaiting_scan."""
    if not wab.bridge_installed(home) and wab.status_or_none(home) is None:
        return {"step": "not_installed", "ok": False, "can_download": True, "message": wab.NOT_INSTALLED_EN,
                "message_he": wab.NOT_INSTALLED_HE}
    ok, why = wab.start_bridge(home)
    if not ok:
        return {"step": "bridge_down", "ok": False, "message": why, "message_he": BridgeError("WA_BRIDGE_DOWN").he}
    st = BridgeClient(home).status()
    if st.get("linked") and st.get("connection") == "connected":
        return {"step": "already_linked", "ok": True, "state": "linked", "mode": st.get("mode")}
    r = admin(home).call("POST", "/link", body={"phone": phone} if phone else {})
    return {**r, "step": "awaiting_scan", "ok": True}


def _install_cli(home: Path) -> int:
    r = install_bridge(home)
    print(clilang.fmt(r.get("message_he", ""), r.get("message", "")))
    if not r["ok"]:
        print(f"{r['code']}")
    return 0 if r["ok"] else 1


def _setup(home: Path, a) -> int:
    """`jarvis whatsapp setup`: the guided path. Says plainly if the bridge program is missing; otherwise starts the
    bridge, links (the phone scan is always yours) and then offers read-only mode. Never links or sends silently."""
    if not wab.bridge_installed(home) and wab.status_or_none(home) is None:
        print(clilang.fmt(wab.NOT_INSTALLED_HE, wab.NOT_INSTALLED_EN))
        if not _confirm(clilang.fmt("להוריד את גשר הוואטסאפ עכשיו (קובץ חתום, נבדק לפני ההתקנה)? [y/N] ",
                                    "Download the WhatsApp bridge now (a signed file, verified before it is installed)? [y/N] ")):
            return 1
        if _install_cli(home) != 0:
            return 1
    rc = _link(home, a)
    if rc != 0:
        return rc
    if _confirm("Switch WhatsApp to read-only mode now, and turn on the WhatsApp reading switch "
                "(it reads, it never sends)? [y/N] "):
        r = enable_reading(home, by="cli")
        print(f"mode: {r.get('mode')}")
        print(clilang.fmt(r["message_he"], r["message"]))
    else:
        print("Mode and the reading switch left as they are. Later: jarvis whatsapp mode read, "
              f"then {SWITCH_CMD}")
    return 0


def _link(home: Path, a) -> int:
    ok, why = wab.start_bridge(home)
    if not ok:
        print(f"{_down_code(home)}: {why}")
        return 1
    print(clilang.fmt(TOS_NOTE_HE, TOS_NOTE))
    st = BridgeClient(home).status()
    if st.get("linked") and st.get("connection") == "connected":
        print("Already linked (status: connected). To link another phone: jarvis whatsapp unlink first.")
        return 0
    ad = admin(home)
    r = ad.call("POST", "/link", body={"phone": a.phone} if a.phone else {})
    if a.window:
        return _qr_window(ad, r)
    print(clilang.fmt("בטלפון: וואטסאפ > הגדרות > מכשירים מקושרים > קישור מכשיר, וסורקים:",
                      "On the phone: WhatsApp > Settings > Linked devices > Link a device, then scan:"))
    shown = ""
    deadline = time.monotonic() + LINK_WAIT_S
    while time.monotonic() < deadline:
        if r.get("pairing_code") and r["pairing_code"] != shown:
            shown = r["pairing_code"]
            print(f"Pairing code: {shown}  (Link with phone number instead, then type it)")
        elif r.get("qr_text") and r.get("qr") != shown:
            shown = r.get("qr", "")
            print(r["qr_text"])
        if r.get("state") == "linked":
            print(clilang.fmt("מקושר. בטלפון יופיע \"JARVES bridge\" במכשירים המקושרים. ניתוק: בטלפון > מכשירים "
                              "מקושרים > JARVES bridge > התנתקות.",
                              "Linked. The phone shows \"JARVES bridge\" under Linked devices. To unlink: phone > "
                              "Linked devices > JARVES bridge > Log out (always works, even if this PC is lost)."))
            print("Next: jarvis whatsapp mode read   (read-only; send mode only after 14 quiet days)")
            return 0
        if r.get("state") in ("timeout", "error"):
            print(f"Linking did not finish ({r.get('state')}). Run jarvis whatsapp link again.")
            return 1
        time.sleep(1.0)
        r = ad.call("GET", "/link")
    print("Linking timed out. Run jarvis whatsapp link again.")
    return 1


def _qr_window(ad: BridgeClient, first: dict) -> int:
    """`jarvis whatsapp link --window`: the QR (or pairing code) in a small window that refreshes itself and closes
    when the phone has linked. Tk from the standard library (same as the approvals popup)."""
    import tkinter as tk
    root = tk.Tk()
    root.title("JARVES - link WhatsApp")
    root.attributes("-topmost", True)
    tk.Label(root, text="WhatsApp > Settings > Linked devices > Link a device", font=("Segoe UI", 11)).pack(pady=6)
    qr = tk.Label(root, font=("Consolas", 7), justify="left", bg="white", fg="black")
    qr.pack(padx=12, pady=6)
    note = tk.Label(root, text="", font=("Segoe UI", 10))
    note.pack(pady=6)
    result = {"rc": 1, "r": first, "deadline": time.monotonic() + LINK_WAIT_S}

    def tick() -> None:
        r = result["r"]
        qr.configure(text=r.get("pairing_code") or r.get("qr_text") or "")
        if r.get("state") == "linked":
            note.configure(text="Linked. The phone shows \"JARVES bridge\". (Unlink there at any time.)")
            result["rc"] = 0
            root.after(1500, root.destroy)
            return
        if r.get("state") in ("timeout", "error") or time.monotonic() > result["deadline"]:
            note.configure(text="Linking did not finish: run jarvis whatsapp link again.")
            root.after(3000, root.destroy)
            return
        try:
            result["r"] = ad.call("GET", "/link")
        except BridgeError as e:
            note.configure(text=e.code)
        root.after(1000, tick)
    tick()
    root.mainloop()
    return result["rc"]


def cmd_whatsapp(a, cfg, home) -> int:
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    home = Path(home)
    args = list(a.args or [])
    try:
        if a.action == "status":
            st = wab.status_or_none(home)
            s, detail, action = wab.health_view(st)
            out = {"status": s, "detail": detail, "action": action, "configured": wab.configured(home),
                   "reading_switch": "on" if reading_switch_on(home) else "off",
                   "bridge_installed": wab.bridge_installed(home), "bridge_download": wbi.status(home),
                   "bridge": None if st is None else {k: st.get(k) for k in (
                       "engine", "version", "mode", "killed", "connection", "linked", "watch_days_left",
                       "retention_days", "keep_nothing", "counts", "rate", "warnings_total", "key_protection")}}
            if out["reading_switch"] == "off":
                out["reading_switch_fix"] = SWITCH_CMD
            _print(out, a.json)
            if out["reading_switch"] == "off" and not a.json:
                print(clilang.fmt(SWITCH_OFF_HE, SWITCH_OFF_EN))
            return 0 if s == "ok" else 1
        if a.action == "start":
            ok, why = wab.start_bridge(home)
            print(("ok: " if ok else "WA_BRIDGE_DOWN: ") + why)
            return 0 if ok else 1
        if a.action == "stop":
            admin(home).call("POST", "/shutdown")
            print("bridge stopping (store sealed)")
            return 0
        if a.action == "install":
            return _install_cli(home)
        if a.action == "link":
            return _link(home, a)
        if a.action == "setup":
            return _setup(home, a)
        if a.action == "unlink":
            if not a.yes and not _confirm("Unlink the JARVES bridge and wipe its store on this PC? [y/N] "):
                print("not changed")
                return 1
            admin(home).call("POST", "/unlink")
            print("Unlinked and wiped. Check the phone: Linked devices should no longer list \"JARVES bridge\" "
                  "(if it does, log it out there).")
            return 0
        if a.action == "mode":
            if len(args) != 1 or args[0] not in pol.MODES:
                print("usage: jarvis whatsapp mode off|read|send", file=sys.stderr)
                return 2
            if args[0] == "send":
                print(clilang.fmt(TOS_NOTE_HE, TOS_NOTE))
                print("Send mode: every message still needs your approval on the screen (recipient, number, full "
                      "text), only to allow-listed people, at most 10 per hour and 30 per day.")
                if not _confirm("Type SEND to switch send mode on: ", "SEND"):
                    print("not changed")
                    return 1
            r = set_mode(home, args[0], override_watch=bool(a.override_watch))
            print(f"mode: {r.get('mode')}")
            if args[0] != "off" and not reading_switch_on(home):
                print(clilang.fmt(SWITCH_OFF_HE, SWITCH_OFF_EN))
            return 0
        if a.action in ("allow", "remove"):
            if len(args) < 2 or args[0] not in ("read", "send"):
                print(f"usage: jarvis whatsapp {a.action} read|send <contact name or +number>", file=sys.stderr)
                return 2
            r = allow(home, args[0], "add" if a.action == "allow" else "remove", " ".join(args[1:]))
            print(f"{args[0]} allow-list: {r.get('count')} chat(s) ({'added' if a.action == 'allow' else 'removed'}: "
                  f"{r.get('label')})")
            return 0
        if a.action == "scope":
            body = {k: v == "on" for k, v in (("groups", a.groups), ("all_direct", a.all_direct)) if v is not None}
            _print(admin(home).call("POST", "/read_scope", body=body), a.json)
            return 0
        if a.action == "inbox":
            user = BridgeClient(home)
            if args:
                hits = wab.resolve(user.chats(None, 500), " ".join(args), wab.contacts_book(home))
                if len(hits) != 1:
                    print("several chats match" if hits else "no chat matches")
                    return 1
                ms = user.messages(hits[0]["id"], None, a.limit).get("messages", [])
                rows = [wab.message_view(m, {hits[0]["id"]: hits[0].get("name", "")}) for m in ms]
                if a.json:
                    _print(rows, True)
                for m in rows if not a.json else []:
                    print(f"[{m['time']}] {m['from']}: {m['text'] if m['kind'] == 'text' else m['media']}"
                          + ("  (hidden characters removed)" if m.get("hidden_chars_removed") else ""))
                return 0
            chats = user.chats(None, 200)
            rows = [{"name": pol.clean_text(c.get("name") or "")[0], "unread": c.get("unread", 0),
                     "last": wab._hhmm(c.get("last_ts", 0)), "can_send": c.get("can_send", False)} for c in chats]
            _print(rows, a.json) if a.json else [print(f"{r['unread']:>3}  {r['last']}  {r['name']}") for r in rows]
            return 0
        if a.action == "send":
            if len(args) < 2:
                print("usage: jarvis whatsapp send <contact> \"<text>\"", file=sys.stderr)
                return 2
            r = send(home, args[0], " ".join(args[1:]))
            print(f"sent ({r.get('id')})")
            return 0
        if a.action == "kill":
            if a.release:
                _print(release(home), a.json)
            else:
                _print(kill(home, logout=a.logout), a.json)
            return 0
        if a.action == "purge":
            if not a.yes and not _confirm("Delete the stored WhatsApp messages"
                                          + (f" older than {a.older_than} days" if a.older_than else "") + "? [y/N] "):
                print("not changed")
                return 1
            _print(admin(home).call("POST", "/purge", body={"older_than_days": a.older_than}), a.json)
            return 0
        if a.action == "retention":
            body: dict = {}
            if args:
                body["days"] = int(args[0])
            if a.keep_nothing is not None:
                body["keep_nothing"] = a.keep_nothing == "on"
            _print(admin(home).call("POST", "/retention", body=body), a.json)
            return 0
        if a.action == "audit":
            _print(admin(home).call("GET", "/audit", params={"limit": a.limit}), a.json)
            return 0
    except BridgeError as e:
        extra = f" ({e.extra})" if e.extra else ""
        print(f"{e.code}: {clilang.fmt(e.he, e.message)}{extra}")
        return 1
    except ValueError:
        print("bad value", file=sys.stderr)
        return 2
    return 2


# ---------------------------------------------------------------------- dashboard
def _local(ctx) -> bool:
    who = getattr(ctx, "principal", None)
    return isinstance(who, dict) and who.get("kind") == "local"


PC_ONLY = (403, {"error": "only the computer itself can change WhatsApp access",
                 "error_he": "את זה משנים רק במחשב עצמו"})


def _ref(jid: str) -> str:
    """Dashboard chat reference: a JID looks like an e-mail address to the dashboard's e-mail masking."""
    return str(jid).replace("@", "~")


def _jid(ref: str) -> str:
    return str(ref or "").replace("~", "@")


def _err(e: BridgeError) -> tuple[int, dict]:
    return (e.status if 400 <= e.status < 600 else 503), {"error": e.message, "code": e.code, "error_he": e.he,
                                                          **e.extra}


def view(ctx) -> dict:
    from .features import _ctx_features
    f = _ctx_features(ctx)
    st = wab.status_or_none(ctx.home)
    s, detail, action = wab.health_view(st)
    out = {"health": {"status": s, "detail": detail, "action": action}, "status": st,
           "reading_switch": {"on": bool(f.enabled(wab.FEATURE)), "fix": SWITCH_CMD, "off_en": SWITCH_OFF_EN,
                              "off_he": SWITCH_OFF_HE},
           "feature": f.status(wab.FEATURE), "send_feature": f.status(wab.SEND_FEATURE),
           "configured": wab.configured(ctx.home), "binary": wab.bridge_installed(ctx.home),
           "bridge_installed": wab.bridge_installed(ctx.home),
           "not_installed": {"en": wab.NOT_INSTALLED_EN, "he": wab.NOT_INSTALLED_HE},
           "bridge_download": wbi.status(ctx.home),
           "tos": TOS_NOTE, "tos_he": TOS_NOTE_HE, "local": _local(ctx)}
    if st is not None and _local(ctx):
        try:
            ad = admin(ctx.home)
            out["policy"] = ad.call("GET", "/policy")
            au = ad.call("GET", "/audit", params={"limit": 30})
            out["audit"] = au.get("lines", au.get("audit", []))
            chats = BridgeClient(ctx.home).chats(None, 200) if st.get("mode") != "off" else []
            names = {c["id"]: c.get("name", "") for c in chats}
            for k in ("read_allow", "send_allow"):
                out["policy"][k] = [{"id": _ref(j), "name": names.get(j, ""), "number": pol.jid_number(j)}
                                    for j in out["policy"].get(k, [])]
            hashes = {pol.chat_hash(j): n for j, n in names.items()}
            for row in out["audit"]:
                row["name"] = hashes.get(row.get("chat_hash", ""), "")
            out["chats"] = [{"id": _ref(c["id"]), "name": pol.clean_text(c.get("name") or "")[0], "unread": c.get("unread", 0),
                             "last_ts": c.get("last_ts"), "is_group": c.get("is_group"), "can_send": c.get("can_send"),
                             "number": c.get("number", "")} for c in chats]
        except BridgeError as e:
            out["error"] = e.code
    return out


def dashboard_routes(register) -> None:
    def get(ctx, _m, _b):
        return 200, view(ctx)

    def messages(ctx, _m, _b, q=None):
        if not _local(ctx):
            return PC_ONLY
        chat = _jid(str(((q or {}).get("chat") or [""])[0]))
        try:
            res = BridgeClient(ctx.home).messages(chat, None, 50)
        except BridgeError as e:
            return _err(e)
        name = (res.get("chat") or {}).get("name", "")
        return 200, {"chat": {"id": _ref(chat), "name": pol.clean_text(name)[0]},
                     "messages": [wab.message_view(m, {chat: name}) for m in res.get("messages", [])],
                     "untrusted": True}

    def post(fn):
        def h(ctx, _m, body):
            if not _local(ctx):
                return PC_ONLY
            try:
                return 200, fn(ctx, body if isinstance(body, dict) else {})
            except BridgeError as e:
                return _err(e)
        return h

    def mode(ctx, b):
        m = b.get("mode")
        if m == "send":
            from . import approvals
            ok, _ = approvals.ask_owner(ctx.home, "Switch WhatsApp to SEND mode? Every message still needs your "
                                        "approval on the screen.", title="whatsapp_mode_send", risk="high",
                                        kind="feature", why="you pressed Send mode on the dashboard WhatsApp page")
            if not ok:
                raise BridgeError("WA_APPROVAL_MISSING", "not approved on the screen", 403)
        if m == "read" and b.get("enable_reading") is True:          # the page's explicit yes after linking
            from .features import _ctx_features
            return enable_reading(ctx.home, by="dashboard", features=_ctx_features(ctx))
        return set_mode(ctx.home, str(m), override_watch=b.get("override_watch") is True, by="dashboard")

    def allow_(ctx, b):
        return allow(ctx.home, str(b.get("list")), str(b.get("op") or "add"), _jid(str(b.get("chat") or "")),
                     by="dashboard")

    def kill_(ctx, b):
        return release(ctx.home, by="dashboard") if b.get("on") is False else kill(ctx.home, logout=b.get("logout") is True,
                                                                                     by="dashboard")

    def link(ctx, b):
        ok, why = wab.start_bridge(ctx.home)
        if not ok:
            raise BridgeError(_down_code(ctx.home), why, 503)
        return admin(ctx.home).call("POST", "/link", body={"phone": b["phone"]} if b.get("phone") else {})

    def link_state(ctx, _m, _b):
        if not _local(ctx):
            return PC_ONLY
        try:
            return 200, admin(ctx.home).call("GET", "/link")
        except BridgeError as e:
            return _err(e)

    def unlink(ctx, b):
        from . import approvals
        ok, _ = approvals.ask_owner(ctx.home, "Unlink the JARVES WhatsApp bridge and wipe its store on this PC?",
                                    title="whatsapp_unlink", risk="high", why="you pressed Unlink on the dashboard")
        if not ok:
            raise BridgeError("WA_APPROVAL_MISSING", "not approved on the screen", 403)
        return admin(ctx.home).call("POST", "/unlink")

    def retention(ctx, b):
        body = {k: b[k] for k in ("days", "keep_nothing") if k in b}
        return admin(ctx.home).call("POST", "/retention", body=body)

    def purge(ctx, b):
        if b.get("confirm") is not True:
            raise BridgeError("WA_BAD_REQUEST", "this cannot be undone: send {\"confirm\": true}", 400)
        return admin(ctx.home).call("POST", "/purge", body={"older_than_days": b.get("older_than_days")})

    def scope(ctx, b):
        return admin(ctx.home).call("POST", "/read_scope", body={k: b[k] for k in ("groups", "all_direct")
                                                                 if isinstance(b.get(k), bool)})

    def setup(ctx, b):
        return setup_begin(ctx.home, str(b.get("phone") or ""))

    def install(ctx, _b):
        return install_bridge(ctx.home)               # owner pressed the button; failures are answers, not crashes

    def start(ctx, _b):
        ok, why = wab.start_bridge(ctx.home)
        if not ok:
            raise BridgeError(_down_code(ctx.home), why, 503)
        return {"ok": True, "detail": why}

    register("GET", r"/api/whatsapp", get)
    register("GET", r"/api/whatsapp/messages", messages)
    register("GET", r"/api/whatsapp/link", link_state)
    for path, fn in (("mode", mode), ("allow", allow_), ("kill", kill_), ("link", link), ("unlink", unlink),
                     ("retention", retention), ("purge", purge), ("scope", scope), ("start", start), ("setup", setup),
                     ("install", install)):
        register("POST", rf"/api/whatsapp/{path}", post(fn))
