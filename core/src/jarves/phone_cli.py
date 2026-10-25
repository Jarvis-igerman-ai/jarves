"""`jarvis phone status | approvals on|off | revoke-all | bind lan|tailnet` (V5 owner decisions 2026-10-21).

The same switches as the dashboard Phone page. Nothing here starts a server or installs anything: the settings take
effect when the phone server starts (bind) or at once (approvals, revoke-all). Tailscale is only DETECTED.
"""
from __future__ import annotations

import json

from . import clilang


def _mob(home):
    from .mobile import MobileAccess
    return MobileAccess(home)


def status_view(cfg, home) -> dict:
    from . import tailnet
    from .cli import _cli_features
    m = _mob(home)
    t = tailnet.detect()
    devs = [d for d in m.devices() if not d.get("revoked")]
    return {"enabled": _cli_features(cfg, home).enabled("mobile_access"), **m.settings(), "paired_phones": len(devs),
            "tailscale": {"state": t["state"], "ip": t["ip"], "name": t["name"], "certs_possible": t["certs_possible"]}}


def cmd_phone(a, cfg, home) -> int:
    from . import logs
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    m = _mob(home)
    if a.action == "approvals":
        m.set_phone_approvals(a.state == "on", by="cli")
        print(clilang.fmt("אישורים מהטלפון: " + ("פועל (בקשות עם קוד נשארות במחשב)" if a.state == "on" else "כבוי"),
                          "phone approvals: " + ("ON (typed-code requests stay on the PC)" if a.state == "on" else "off")))
        return 0
    if a.action == "revoke-all":
        n = m.revoke_all(by="cli")
        print(clilang.fmt(f"בוטלה הגישה של {n} טלפונים", f"revoked {n} phone(s)"))
        return 0
    if a.action == "scopes":
        from .mobile import ALL_SCOPES, DEFAULT_SCOPES
        devs = [d for d in m.devices() if not d.get("revoked")]
        if a.device:
            hit = next((d for d in devs if d["id"] == a.device), None)
            if hit is None:
                print(clilang.fmt("אין טלפון כזה (או שבוטל)", "no such paired phone (or it was revoked)"))
                return 1
            cur = list(hit.get("scopes") or DEFAULT_SCOPES)
            for sc in a.grant or []:
                cur = cur if sc in cur else cur + [sc]
            cur = [x for x in cur if x not in (a.revoke or [])]
            if a.grant or a.revoke:
                m.set_scopes(a.device, cur, by="cli")
            devs = [dict(hit, scopes=[x for x in ALL_SCOPES if x in cur])]
        for d in devs:
            print(f"{d['id']}  {d['name']}: " + ", ".join(d.get("scopes") or DEFAULT_SCOPES))
        return 0
    if a.action == "bind":
        m.set_bind_tailnet_only(a.where == "tailnet", by="cli")
        print(clilang.fmt("נשמר. ייכנס לתוקף בהפעלה הבאה של שרת הטלפון.", "Saved. It applies when the phone server starts next."))
        return 0
    v = status_view(cfg, home)
    if a.json:
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return 0
    t = v["tailscale"]
    print(clilang.fmt(f"גישה מהטלפון: {'פועלת' if v['enabled'] else 'כבויה'}; טלפונים מצומדים: {v['paired_phones']}",
                      f"phone access: {'on' if v['enabled'] else 'off'}; paired phones: {v['paired_phones']}"))
    print(clilang.fmt("אישורים מהטלפון: " + ("פועל" if v["phone_approvals"] else "כבוי"),
                      "phone approvals: " + ("on" if v["phone_approvals"] else "off")))
    print(clilang.fmt("האזנה: " + ("כתובת Tailscale בלבד" if v["bind_tailnet_only"] else "רשת הבית (וכן Tailscale אם קיים)"),
                      "listening: " + ("tailnet address only" if v["bind_tailnet_only"] else "home network (and Tailscale if present)")))
    word = {"ready": ("מוכן", "ready"), "not-installed": ("לא מותקן", "not installed"),
            "not-connected": ("לא מחובר", "not connected")}[t["state"]]
    print(clilang.fmt(f"Tailscale: {word[0]}" + (f" ({t['ip']}, {t['name'] or 'ללא שם'})" if t["ip"] else ""),
                      f"Tailscale: {word[1]}" + (f" ({t['ip']}, {t['name'] or 'no name'})" if t["ip"] else "")))
    return 0


def add_cli(sub) -> None:
    p = sub.add_parser("phone", help="phone access: status (incl. Tailscale), approvals on|off, revoke-all, bind lan|tailnet")
    ps = p.add_subparsers(dest="action", required=True)
    st = ps.add_parser("status", help="phone access, paired phones, phone approvals, Tailscale detection")
    st.add_argument("--json", action="store_true")
    ap = ps.add_parser("approvals", help="let paired phones approve actions (typed-code requests stay on the PC)")
    ap.add_argument("state", choices=["on", "off"])
    ps.add_parser("revoke-all", help="revoke every paired phone at once (a lost phone)")
    sc = ps.add_parser("scopes", help="what each paired phone may use; --grant/--revoke tasks (goals, notice snooze) or prefs (learned preferences)")
    sc.add_argument("device", nargs="?", default="")
    sc.add_argument("--grant", action="append", choices=["tasks", "prefs"])
    sc.add_argument("--revoke", action="append", choices=["tasks", "prefs"])
    b = ps.add_parser("bind", help="listen on the home network (lan) or on the Tailscale address only (tailnet)")
    b.add_argument("where", choices=["lan", "tailnet"])


COMMANDS = {"phone": cmd_phone}
