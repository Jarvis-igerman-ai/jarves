"""`jarvis phone status | approvals on|off | revoke-all | bind lan|tailnet | trust ...` (V5 owner decisions 2026-10-21; trust: #513).

The same switches as the dashboard Phone page. Nothing here starts a server or installs anything: the settings take
effect when the phone server starts (bind) or at once (approvals, revoke-all). Tailscale is only DETECTED.
"""
from __future__ import annotations

import json
import sys

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


TRUST_MEANING = (
    ("מה זה אומר לסמוך על רשות אישורים: מי שמחזיק את המפתח שלה יכול ליצור אישורים שהטלפון יקבל. כאן המפתח נשאר במחשב הזה בלבד "
     "(הרשאות לבעלים בלבד), והרשות מוגבלת בתוך האישור שלה לכתובות ברשת פרטית ולשמות מקומיים, כך שהיא לא יכולה לאשר אתר ציבורי. "
     "שימו לב: היא כן יכולה לאשר כל כתובת פרטית או שם מקומי, בכל רשת Wi-Fi שהטלפון מחובר אליה. Chrome ואנדרואיד אוכפים את "
     "ההגבלה; אפליקציות מסוימות אולי לא. התקינו רק בטלפון שלכם; לביטול: \"jarvis phone trust revoke\" ואז הסרה בטלפון."),
    ("What trusting a CA means: whoever holds its key can make certificates the phone accepts. Here the key stays on this PC only "
     "(owner-only permissions), and the CA is limited inside its own certificate to private-network addresses and local names, so it "
     "cannot vouch for a public site. Note: it CAN vouch for any private address or local name, on any Wi-Fi the phone joins. Chrome "
     "and Android enforce the limit; some apps may not. Install it only on your own phone; to undo: jarvis phone trust revoke, then "
     "remove it on the phone."))
ANDROID_STEPS = (
    ("בטלפון (אנדרואיד): הורידו את הקובץ מעמוד הטלפון המצומד (או העבירו את הקובץ), ואז הגדרות > אבטחה (או אבטחה ופרטיות > אבטחה נוספת) > "
     "התקנת אישור (הצפנה ופרטי כניסה) > אישור CA > \"התקן בכל זאת\" ובחרו את הקובץ. אנדרואיד יציג אזהרה שהרשת עשויה להיות מנוטרת: זו "
     "האזהרה הכללית לכל אישור CA ידני."),
    ("On the phone (Android): download the file from the paired phone page (or copy it over), then Settings > Security (or Security and "
     "privacy > More security) > Install a certificate (Encryption and credentials) > CA certificate > \"Install anyway\" and pick the file. "
     "Android shows a warning that the network may be monitored: that is its generic text for any manual CA."))
REMOVE_STEPS = (
    ("להסרה בטלפון: הגדרות > אבטחה > הצפנה ופרטי כניסה > פרטי כניסה של משתמש > \"JARVES local CA\" > הסרה."),
    ("To remove it on the phone: Settings > Security > Encryption and credentials > User credentials > \"JARVES local CA\" > Remove."))
# Path B (#513): guidance only. JARVES never installs Tailscale, never signs in and never sees its keys; `tailscale cert` is run
# by the phone server at start (tailnet.choose_tls) once the owner enabled HTTPS certificates for the tailnet.
TAILSCALE_STEPS = {
    "not-installed": ("נתיב ב': התקינו את Tailscale במחשב ובטלפון מהאתר או מחנות האפליקציות שלו, והתחברו בשני המכשירים לאותו חשבון "
                      "באפליקציה של Tailscale עצמה (JARVES לא רואה את פרטי הכניסה). אחר כך הריצו שוב: \"jarvis phone trust tailscale\"",
                      "Path B: install Tailscale on the computer and on the phone (its own site or app store) and sign in on both to the "
                      "same account in Tailscale's own app (JARVES never sees the sign-in). Then run again: jarvis phone trust tailscale"),
    "not-connected": ("Tailscale מותקן אבל לא מחובר. התחברו באפליקציה של Tailscale במחשב, ואז הריצו שוב: \"jarvis phone trust tailscale\"",
                      "Tailscale is installed but not connected. Sign in in the Tailscale app on this computer, then run again: "
                      "jarvis phone trust tailscale"),
    "no-name": ("Tailscale מחובר, אבל אין שם רשת למחשב. במסוף הניהול של Tailscale (DNS) הפעילו את MagicDNS ואת \"HTTPS Certificates\", "
                "ואז הריצו שוב: \"jarvis phone trust tailscale\"",
                "Tailscale is connected but this computer has no tailnet name. In the Tailscale admin console (DNS) turn on MagicDNS and "
                "\"HTTPS Certificates\", then run again: jarvis phone trust tailscale"),
    "ready": ("Tailscale מוכן. ודאו שבמסוף הניהול של Tailscale (DNS) מופעל \"HTTPS Certificates\". בהפעלה הבאה של שרת הטלפון JARVES מבקש "
              "מ-Tailscale אישור אמיתי לשם {name} (בלי להתקין כלום בטלפון). אפשר גם: \"jarvis phone bind tailnet\". בטלפון, כש-Tailscale "
              "מחובר, פתחו את https://{name}:{port}/m/",
              "Tailscale is ready. Make sure \"HTTPS Certificates\" is on in the Tailscale admin console (DNS). At the next phone server "
              "start JARVES asks Tailscale for a real certificate for {name} (nothing to install on the phone). Optional: jarvis phone "
              "bind tailnet. On the phone, with Tailscale connected, open https://{name}:{port}/m/"),
}


def _confirm(a, he: str, en: str) -> bool:
    """High-risk trust actions need the owner's explicit yes: --yes, or typing yes at an interactive terminal."""
    if getattr(a, "yes", False):
        return True
    print(clilang.fmt(he, en))
    try:
        if not sys.stdin.isatty():
            print(clilang.fmt("לא אושר (אין מסוף). הוסיפו --yes כדי לאשר.", "Not approved (no terminal). Add --yes to approve."),
                  file=sys.stderr)
            return False
        ok = input(clilang.fmt("הקלידו yes לאישור: ", "Type yes to confirm: ")).strip().lower() == "yes"
    except (EOFError, OSError, ValueError):
        ok = False
    if not ok:
        print(clilang.fmt("בוטל. שום דבר לא השתנה.", "Cancelled. Nothing changed."), file=sys.stderr)
    return ok


def _feature_off_msg() -> None:
    print(clilang.fmt("התכונה כבויה (בטא). הריצו: \"jarvis features on beta_features\" ואז \"jarvis features on phone_trust\"",
                      "the feature is off (beta). Run: jarvis features on beta_features, then jarvis features on phone_trust"),
          file=sys.stderr)


def _print_ca(st: dict) -> None:
    print(clilang.fmt(f"קובץ האישור (ציבורי): {st['ca_file']}\nטביעת אצבע SHA-256: {st['fingerprint']}",
                      f"public certificate file: {st['ca_file']}\nSHA-256 fingerprint: {st['fingerprint']}"))


def _tailscale(a) -> int:
    from . import tailnet
    from .dashboard_auth import LAN_PORT
    t = tailnet.detect()
    state = t["state"] if t["state"] != "ready" else ("ready" if t["name"] else "no-name")
    if a.json:
        print(json.dumps({"state": state, "ip": t["ip"], "name": t["name"], "certs_possible": t["certs_possible"]},
                         ensure_ascii=False, indent=1))
        return 0
    he, en = TAILSCALE_STEPS[state]
    name = t["name"] or ""
    print(clilang.fmt(he.format(name=name, port=LAN_PORT), en.format(name=name, port=LAN_PORT)))
    return 0 if state == "ready" else 1


def _trust(a, cfg, home) -> int:
    from . import phone_trust as pt
    act = a.trust_action
    if act == "status":
        st = pt.status(home)
        st["feature_on"] = pt.trust_on(home)
        if a.json:
            print(json.dumps(st, ensure_ascii=False, indent=1))
            return 0
        on = st["usable"] and st["feature_on"]                  # an expired CA is never "on"
        print(clilang.fmt("חיבור מהימן: " + ("פועל" if on else "כבוי"), "trusted connection: " + ("on" if on else "off")))
        if st["ca"]:
            print(clilang.fmt(f"רשות אישורים תקפה עד {st['ca_expires']}; טביעת אצבע SHA-256: {st['fingerprint']}",
                              f"CA valid until {st['ca_expires']}; SHA-256 fingerprint: {st['fingerprint']}"))
            print(clilang.fmt(f"אישור השרת בתוקף עד {st['leaf_expires'] or 'יונפק בהפעלה הבאה'}",
                              f"server certificate valid until {st['leaf_expires'] or 'issued at the next phone server start'}"))
            if st["renew_checked"]:
                print(clilang.fmt(f"בדיקת חידוש אחרונה: {st['renew_checked'][:16]}", f"last renewal check: {st['renew_checked'][:16]}"))
            for w in st["warnings"]:
                he, en = pt.WARNING_TEXT[w]
                print(clilang.fmt("אזהרה: " + he, "WARNING: " + en))
        elif st["ca_cert_without_key"]:
            print(clilang.fmt("יש אישור בלי מפתח (למשל אחרי שחזור מגיבוי: המפתח לעולם לא נשמר בגיבוי). הריצו \"jarvis phone trust rotate\"",
                              "a certificate without its key (for example after a restore: the key is never backed up). Run "
                              "jarvis phone trust rotate"))
        else:
            print(clilang.fmt("אין רשות אישורים. להפעלה: \"jarvis features on phone_trust\" ואז \"jarvis phone trust enable\"",
                              "no CA yet. To start: jarvis features on phone_trust, then jarvis phone trust enable"))
        return 0
    if act == "tailscale":
        return _tailscale(a)
    if act in ("enable", "rotate"):
        if not pt.trust_on(home):
            _feature_off_msg()
            return 1
        cur = pt.status(home)
        if act == "enable" and cur["ca"]:
            _print_ca(cur)
            print(clilang.fmt("הרשות כבר קיימת; לא נוצרה חדשה. להחלפה: \"jarvis phone trust rotate\"",
                              "the CA already exists; nothing new was made. To replace it: jarvis phone trust rotate"))
            pt.ensure_leaf(home)
            return 0
        print(clilang.fmt(TRUST_MEANING[0], TRUST_MEANING[1]))
        q = (("ליצור רשות אישורים מקומית במחשב הזה?", "Make a local certificate authority on this computer?") if act == "enable" else
             ("להחליף את הרשות? הישנה תימחק, והטלפון יצטרך להסיר את הישנה ולהתקין את החדשה.",
              "Replace the CA? The old one is deleted, and the phone must remove the old certificate and install the new one."))
        if not _confirm(a, *q):
            return 1
        st = pt.create_ca(home) if act == "enable" else pt.rotate(home)
        pt.ensure_leaf(home)
        _print_ca(st)
        if act == "rotate" and st.get("previous"):
            print(clilang.fmt(f"טביעת האצבע הקודמת: {st['previous']}", f"previous fingerprint: {st['previous']}"))
            print(clilang.fmt(REMOVE_STEPS[0], REMOVE_STEPS[1]))
        print(clilang.fmt(ANDROID_STEPS[0], ANDROID_STEPS[1]))
        print(clilang.fmt("הפעילו מחדש את שרת הטלפון כדי שישתמש באישור החדש, ואז: \"jarvis phone trust verify\"",
                          "Restart the phone server so it uses the new certificate, then: jarvis phone trust verify"))
        return 0
    if act == "renew":
        got = pt.ensure_leaf(home)
        print(clilang.fmt("אישור השרת עדכני" if got else "אין רשות אישורים", "server certificate is current" if got else "no CA"))
        return 0 if got else 1
    if act == "ca-path":
        crt, _key = pt.ca_paths(home)
        if not crt.is_file():
            print(clilang.fmt("אין רשות אישורים", "no CA"), file=sys.stderr)
            return 1
        print(crt)                                # the PUBLIC certificate only, never the key path
        return 0
    if act == "verify":
        from .dashboard_auth import LAN_PORT, lan_ip
        host, port = a.host or lan_ip() or "127.0.0.1", a.port or LAN_PORT
        ok, why = pt.verify_listener(home, host, port)
        print(clilang.fmt(("תקין: " if ok else "לא תקין: ") + why, ("OK: " if ok else "NOT trusted: ") + why))
        return 0 if ok else 1
    if act == "revoke":
        if not (pt.ca_paths(home)[0].is_file() or pt.ca_paths(home)[1].is_file() or pt.leaf_paths(home)[0].is_file()):
            print(clilang.fmt("אין רשות אישורים; אין מה לבטל.", "no CA; nothing to revoke."))
            return 0
        if not _confirm(a, "למחוק את הרשות ואת המפתח שלה? השרת יחזור לאישור העצמי.",
                        "Delete the CA and its key? The server falls back to the self-signed certificate."):
            return 1
        r = pt.revoke(home)
        print(clilang.fmt(f"הרשות נמחקה ({r['removed']} קבצים). שרת הטלפון שכבר רץ ממשיך עם האישור הנוכחי עד שמפעילים אותו "
                          "מחדש; אחר כך הוא חוזר לאישור העצמי.",
                          f"CA removed ({r['removed']} files). A phone server that is already running keeps the current certificate "
                          "until it restarts; then it falls back to the self-signed certificate."))
        print(clilang.fmt(REMOVE_STEPS[0], REMOVE_STEPS[1]))
        return 0
    return 2


def cmd_phone(a, cfg, home) -> int:
    from . import logs
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    if a.action == "trust":
        return _trust(a, cfg, home)
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
    if not v["enabled"]:       # switch OFF: no phone listener exists, so never describe one (V5-CORE-WIRING C1/C2)
        print(clilang.fmt("האזנה: אין. גישה מהטלפון כבויה ולא נפתח שום נתיב לטלפון",
                          "listening: nothing (phone access is off, no phone path is open)"))
    else:
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
    sc = ps.add_parser("scopes", help="what each paired phone may use; --grant/--revoke tasks (goals, notice snooze), prefs (learned preferences) or drive (the read-mostly Drive tab)")
    sc.add_argument("device", nargs="?", default="")
    sc.add_argument("--grant", action="append", choices=["tasks", "prefs", "drive"])
    sc.add_argument("--revoke", action="append", choices=["tasks", "prefs", "drive"])
    b = ps.add_parser("bind", help="listen on the home network (lan) or on the Tailscale address only (tailnet)")
    b.add_argument("where", choices=["lan", "tailnet"])
    t = ps.add_parser("trust", help="trusted https for the phone app (beta, off by default): a local certificate authority "
                                    "(status | enable | rotate | renew | ca-path | verify | revoke) or Tailscale (tailscale)")
    ts = t.add_subparsers(dest="trust_action", required=True)
    tst = ts.add_parser("status", help="CA state, expiry and fingerprint")
    tst.add_argument("--json", action="store_true")
    te = ts.add_parser("enable", help="make the JARVES local CA (private-network names only) and print the phone steps")
    te.add_argument("--yes", action="store_true", help="you already approved (no question)")
    tr = ts.add_parser("rotate", help="replace the CA with a new one (new key and fingerprint); the phone installs the new one")
    tr.add_argument("--yes", action="store_true", help="you already approved (no question)")
    ts.add_parser("renew", help="issue a fresh short-lived server certificate when needed")
    ts.add_parser("ca-path", help="print the path of the PUBLIC CA certificate to copy to the phone")
    tv = ts.add_parser("verify", help="check from this PC that the running phone server presents a certificate the JARVES CA signed")
    tv.add_argument("--host", default="", help="default: this computer's home-network address")
    tv.add_argument("--port", type=int, default=0, help="default: the phone server port")
    tk = ts.add_parser("revoke", help="delete the CA key, certificate and server certificate (then remove it on the phone)")
    tk.add_argument("--yes", action="store_true", help="you already approved (no question)")
    tt = ts.add_parser("tailscale", help="path B: guided steps for a real Tailscale certificate (detection only, nothing installed)")
    tt.add_argument("--json", action="store_true")


COMMANDS = {"phone": cmd_phone}
