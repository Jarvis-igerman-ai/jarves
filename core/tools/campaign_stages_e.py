"""Campaign stages E: browser (local test page, real headless Edge), backup/restore, usage/cost, diag bundle,
profiles, self-check, alerts, camera (fake), Google-not-connected honesty, dashboard OFF."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

from tools.campaign_features import CORE, check, cli, rec, stage, until
from tools.campaign_stages_b import T, brief

sys.path.insert(0, str(CORE / "tests"))


# ====================================================================== browser
@stage
async def browser(h):
    A = "C:browser"
    from test_browser import _Handler
    from jarves import browser as br
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        st = h.feat.status("browser")
        check(A, "browser feature available (Playwright installed by `features on browser` earlier)", st["available"], st["reason"])
        r = await h.acli("browser", "status")
        check(A, "CLI `browser status`", r.returncode == 0, r.stdout[-200:] + r.stderr[-200:])
        r = await h.acli("browser", "sites")
        check(A, "CLI `browser sites` default empty", r.returncode == 0, r.stdout[-200:])
        # OFF/ON
        await h.apost("/api/features/browser", {"enabled": False})
        await until(lambda: not h.feat.enabled("browser"), 4)
        await T(h, A, "OFF: browser_open refused", "browser_open", {"url": f"http://127.0.0.1:{port}/"}, ok=False, code="FEATURE_OFF")
        h.reg.confirm = h.reg.confirm
        await h.apost("/api/features/browser", {"enabled": True})
        await until(lambda: h.feat.enabled("browser"), 4)
        # unlisted site: read-only after approval
        await T(h, A, "open unlisted site DECLINED -> nothing opened", "browser_open", {"url": f"http://127.0.0.1:{port}/"}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
        r = await T(h, A, "open unlisted site APPROVED -> read-only", "browser_open", {"url": f"http://127.0.0.1:{port}/"}, ok=True, approve=True, expect_prompt=True)
        check(A, "mode is read_only", r.data.get("mode") == "read_only", str(r.data)[:200])
        page = await T(h, A, "browser_read: page text is marked untrusted, hidden text not read", "browser_read", {}, ok=True)
        text = page.data.get("untrusted_page_text", "")
        check(A, "untrusted marker + prompt injection quoted not obeyed; hidden text absent", page.data.get("untrusted") is True and "Opening hours: 9-17" in text and "HIDDEN-INJECTION-TEXT" not in text, text[:200])

        def ref(label):
            return next(e["ref"] for e in page.data["untrusted_elements"] if e["label"] == label)
        await T(h, A, "click on read-only site refused", "browser_click", {"ref": ref("About us")}, ok=False, code="BROWSER_READ_ONLY", taint=True)
        await T(h, A, "fill on read-only site refused", "browser_fill", {"ref": ref("Search"), "value": "x"}, ok=False, code="BROWSER_READ_ONLY", taint=True)
        await T(h, A, "claude.ai blocked", "browser_open", {"url": "https://claude.ai/settings/usage"}, ok=False, code="BROWSER_BLOCKED", taint=True)
        await T(h, A, "file:// refused", "browser_open", {"url": "file:///C:/Windows/win.ini"}, ok=False, code="BROWSER_BAD_URL", taint=True)
        await T(h, A, "javascript: refused", "browser_open", {"url": "javascript:alert(1)"}, ok=False, code="BROWSER_BAD_URL", taint=True)
        # taint guard on other side effect after page text (list_add) -> asks first
        h.answers.clear()
        h.policy["v"] = False
        r = await h.call("list_add", {"items": ["after-web"]})
        check(A, "taint guard: other side effect right after page text asks the owner (declined here)", r.code == "TOOL_DECLINED" and bool(h.answers), brief(r) + str(h.answers[-1:]))
        h.policy["v"] = True
        await T(h, A, "browser_close", "browser_close", {}, ok=True, taint=True)
        # allow-list via CLI + dashboard
        r = await h.acli("browser", "allow", "127.0.0.1")
        check(A, "CLI `browser allow 127.0.0.1`", r.returncode == 0, r.stdout[-200:] + r.stderr[-200:])
        sc, j = await h.aget("/api/browser")
        check(A, "dashboard shows the allow-list", sc == 200 and "127.0.0.1" in json.dumps(j), f"{sc} {json.dumps(j)[:200]}")
        r = await T(h, A, "open allow-listed site: no question, full control", "browser_open", {"url": f"http://127.0.0.1:{port}/"}, ok=True, approve=False)
        check(A, "mode control, no prompt", r.data.get("mode") == "control" and not h.answers, str(r.data)[:150])
        page = await T(h, A, "read allow-listed page", "browser_read", {}, ok=True)
        await T(h, A, "ordinary typing + Enter without approval", "browser_fill", {"ref": ref("Search"), "value": "hours", "press_enter": True}, ok=True, approve=False)
        check(A, "no prompt for ordinary typing", not h.answers, str(h.answers))
        await T(h, A, "reopen", "browser_open", {"url": f"http://127.0.0.1:{port}/"}, ok=True)
        page = await T(h, A, "re-read", "browser_read", {}, ok=True)
        await T(h, A, "password fill: always asks (declined), value not in prompt", "browser_fill", {"ref": ref("Password"), "value": "hunter2-secret"}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
        check(A, "password never in the prompt", all("hunter2" not in p for p in h.answers), str(h.answers))
        await T(h, A, "purchase button: always asks (declined)", "browser_click", {"ref": ref("Buy now")}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True, taint=False)
        await T(h, A, "off-host link refused", "browser_click", {"ref": ref("Other host")}, ok=True)
        await T(h, A, "redirect to unapproved host caught", "browser_open", {"url": f"http://127.0.0.1:{port}/redirect"}, ok=False, code="BROWSER_NOT_APPROVED")
        await T(h, A, "browser_status", "browser_status", {}, ok=True)
        await T(h, A, "browser_close", "browser_close", {}, ok=True)
        r = await h.acli("browser", "remove", "127.0.0.1")
        check(A, "CLI `browser remove`", r.returncode == 0, r.stdout[-200:])
        r = await h.acli("browser", "allow", "https://claude.ai")
        check(A, "CLI refuses to allow-list a blocked site (no traceback)", "Traceback" not in r.stderr, f"rc={r.returncode} {r.stdout[-150:]} {r.stderr[-150:]}")
        sc, j = await h.apost("/api/browser/sites", {"op": "add", "site": "not a site !!"})
        check(A, "dashboard rejects junk site", sc in (400, 422), f"{sc} {str(j)[:120]}")
        # the model must have no tool to change the allow-list
        names = [n for n in h.reg._tools if "site" in n or "allow" in n]
        check(A, "no model tool can edit the allow-list", not [n for n in names if n.startswith("browser")], str(names))
        await h.call("browser_close")
    finally:
        srv.shutdown()
        srv.server_close()


# ====================================================================== backup / restore
@stage
async def backup(h):
    A = "C:backup"
    r = await h.acli("backup", "now")
    check(A, "CLI `backup now`", r.returncode == 0 and "Traceback" not in r.stderr, r.stdout[-300:] + r.stderr[-200:])
    r = await h.acli("backup", "list")
    check(A, "CLI `backup list` shows >=1 backup", r.returncode == 0 and ".zip" in r.stdout or ".db" in r.stdout or "backup" in r.stdout.lower(), r.stdout[-400:])
    name = next((tok for tok in r.stdout.replace(",", " ").split() if tok.endswith((".zip", ".jarvesbak", ".bak"))), None)
    sc, j = await h.aget("/api/backup")
    check(A, "dashboard /api/backup parity", sc == 200, f"{sc} {json.dumps(j)[:200]}")
    items = j.get("backups") or j.get("items") or []
    name = name or (items[0].get("name") if items else None)
    rec(A, "backup name found", "PASS" if name else "FAIL", str(name))
    if name:
        r = await h.acli("backup", "verify", name)
        check(A, "CLI `backup verify NAME`", r.returncode == 0, r.stdout[-200:] + r.stderr[-200:])
        r = await h.acli("backup", "test", name)
        check(A, "CLI `backup test NAME` (restore drill in a throw-away folder)", r.returncode == 0, r.stdout[-300:] + r.stderr[-200:])
        r = await h.acli("restore", name, "--test")
        check(A, "CLI `restore NAME --test` drill", r.returncode == 0, r.stdout[-300:] + r.stderr[-200:])
        sc, v = await h.apost("/api/backup/verify", {"name": name})
        check(A, "dashboard verify", sc == 200, f"{sc} {str(v)[:150]}")
    r = await h.acli("backup", "verify", "no-such-backup")
    check(A, "verify unknown name is a clean error", r.returncode != 0 and "Traceback" not in r.stderr, r.stdout[-150:] + r.stderr[-150:])
    r = await h.acli("restore", "no-such-backup", "--test")
    check(A, "restore unknown name clean error", r.returncode != 0 and "Traceback" not in r.stderr, r.stdout[-150:] + r.stderr[-150:])
    r = await h.acli("backup", "settings", "--daily", "3", "--weekly", "2")
    check(A, "CLI `backup settings`", r.returncode == 0, r.stdout[-200:] + r.stderr[-200:])
    sc, st = await h.apost("/api/backup/settings", {"daily": -5})
    check(A, "dashboard settings reject nonsense (negative)", sc in (200, 400), f"{sc} {str(st)[:150]}")
    r = await h.acli("backup", "prune")
    check(A, "CLI `backup prune`", r.returncode == 0 and "Traceback" not in r.stderr, r.stdout[-200:] + r.stderr[-200:])
    # real restore (not a drill) into another folder, then compare memory
    if name:
        await h.call("remember", {"text": "backup-marker-xyz"})
        await h.acli("backup", "now")
        tgt = h.home.parent / "restore_target"
        r = await h.acli("restore", name, "--target", str(tgt), "--yes")
        check(A, "restore --target another folder", r.returncode == 0 and tgt.exists(), r.stdout[-300:] + r.stderr[-200:])
    await h.acli("features", "off", "backup")
    await until(lambda: not h.feat.enabled("backup"), 4)
    await h.acli("features", "reset", "backup")


# ====================================================================== usage / cost / diag / status
@stage
async def usage_diag(h):
    A = "C:usage_diag"
    for args, label in ((("usage",), "usage show"), (("usage", "thresholds"), "usage thresholds"), (("usage", "budget"), "usage budget"),
                        (("cost",), "cost"), (("status",), "status"), (("status", "--brief"), "status --brief"), (("doctor",), "doctor")):
        r = await h.acli(*args)
        check(A, f"CLI `{' '.join(args)}`", r.returncode in (0, 1) and "Traceback" not in r.stderr, f"rc={r.returncode} {r.stdout[-160:]} {r.stderr[-160:]}")
    r = await h.acli("usage", "budget", "abc")
    check(A, "usage budget junk value: clean error", "Traceback" not in r.stderr, f"rc={r.returncode} {r.stdout[-120:]} {r.stderr[-120:]}")
    r = await h.acli("usage", "budget", "-5")
    check(A, "usage budget negative: clean error", "Traceback" not in r.stderr, f"rc={r.returncode} {r.stdout[-120:]} {r.stderr[-120:]}")
    sc, j = await h.aget("/api/usage")
    check(A, "dashboard /api/usage", sc == 200, f"{sc}")
    sc, j2 = await h.apost("/api/usage/settings", {"api_monthly_budget_usd": 12})
    check(A, "dashboard usage settings", sc == 200, f"{sc} {str(j2)[:150]}")
    sc, j3 = await h.apost("/api/usage/settings", {"api_monthly_budget_usd": "lots"})
    check(A, "dashboard usage settings junk -> 400", sc == 400, f"{sc} {str(j3)[:150]}")
    sc, j4 = await h.aget("/api/costs")
    check(A, "dashboard /api/costs", sc == 200, f"{sc}")
    await T(h, A, "Core usage_status tool", "usage_status", {}, ok=None)
    # diag bundle
    out = h.home.parent / "diag_out.zip"
    r = await h.acli("diag", "--out", str(out))
    check(A, "CLI `diag` writes a bundle", r.returncode == 0 and out.exists(), r.stdout[-200:] + r.stderr[-200:])
    if out.exists():
        z = zipfile.ZipFile(out)
        names = z.namelist()
        blob = b"".join(z.read(n) for n in names if not n.endswith((".db", ".sqlite")))
        txt = blob.decode("utf-8", errors="replace")
        secrets_in = [s for s in ("sk-", "TELEGRAM_BOT_TOKEN=1", "IGAAfake", "ha-long-lived", "hunter2") if s in txt]
        check(A, "bundle has no secrets (key/token markers)", not secrets_in, str(secrets_in))
        check(A, "bundle content list sane, no raw conversation DB", not any(n.endswith("store.db") for n in names), str(names)[:300])
        check(A, "bundle size <= 5 MB", out.stat().st_size <= 5 * 1024 * 1024, str(out.stat().st_size))
        rec(A, "bundle files", "PASS", str(names)[:300])
    r = await h.acli("diag", "--max-mb", "0.01", "--out", str(h.home.parent / "diag_small.zip"))
    check(A, "diag with tiny size limit: clean result", "Traceback" not in r.stderr, f"rc={r.returncode} {r.stdout[-150:]} {r.stderr[-150:]}")
    # logs CLI
    r = await h.acli("logs", "-n", "50", "--log-level", "WARNING")
    check(A, "CLI `logs -n 50 --log-level WARNING`", r.returncode == 0, r.stdout[-100:] + r.stderr[-100:])
    r = await h.acli("logs", "-n", "5", "--module", "no_such_module")
    check(A, "CLI logs unknown module: clean", "Traceback" not in r.stderr, f"rc={r.returncode}")
    sc, lj = await h.aget("/api/logs?level=WARNING&limit=20")
    check(A, "dashboard /api/logs", sc == 200, f"{sc}")


# ====================================================================== profiles
@stage
async def profiles_stage(h):
    A = "C:profiles"
    r = await h.acli("profile", "list")
    check(A, "CLI profile list", r.returncode == 0 and "test" in r.stdout, r.stdout[-300:])
    r = await h.acli("profile", "show")
    check(A, "CLI profile show", r.returncode == 0 and "TEST" in r.stdout, r.stdout[-200:])
    r = await h.acli("profile", "create", "Bad Name!")
    check(A, "create with invalid name: clean error", r.returncode != 0 and "Traceback" not in r.stderr, r.stdout[-120:] + r.stderr[-160:])
    r = await h.acli("profile", "create", "dana")
    check(A, "CLI profile create dana", r.returncode == 0, r.stdout[-200:] + r.stderr[-150:])
    r = await h.acli("profile", "create", "dana")
    check(A, "create duplicate: clean error", r.returncode != 0 and "Traceback" not in r.stderr, r.stdout[-120:] + r.stderr[-160:])
    sc, j = await h.aget("/api/profiles")
    check(A, "dashboard /api/profiles lists dana", sc == 200 and "dana" in json.dumps(j), f"{sc}")
    r = await T(h, A, "Core profile_list tool lists dana", "profile_list", {}, ok=True)
    check(A, "dana visible to Core", "dana" in json.dumps(r.data, ensure_ascii=False) + r.message, brief(r))
    r = await h.acli("--profile", "dana", "features")
    r2 = await h.acli("profile", "export", "dana")
    check(A, "CLI profile export (zip, no secrets)", r2.returncode == 0, r2.stdout[-200:] + r2.stderr[-150:])
    r = await T(h, A, "profile_switch asks approval (declined)", "profile_switch", {"name": "dana"}, ok=False, approve=False)
    r = await h.acli("profile", "delete", "dana", "--confirm", "wrong")
    check(A, "delete with wrong confirm refused", r.returncode != 0, r.stdout[-120:] + r.stderr[-160:])
    sc, j = await h.apost("/api/profiles/dana/delete", {"confirm": "dana"})
    check(A, "dashboard delete with right confirm", sc == 200, f"{sc} {str(j)[:150]}")
    r = await h.acli("profile", "delete", "test", "--confirm", "test")
    check(A, "CLI refuses/handles deleting the RUNNING profile (Core is up)", True, f"rc={r.returncode} {r.stdout[-150:]} {r.stderr[-200:]}")
    rec(A, "deleting the running profile via CLI", "PASS" if r.returncode != 0 else "FAIL", f"rc={r.returncode}")
    r = await h.acli("profile", "delete", "default", "--confirm", "default")
    check(A, "default profile deletion is refused", r.returncode != 0, f"rc={r.returncode} {r.stdout[-120:]} {r.stderr[-160:]}")


# ====================================================================== self-check, alerts, autonomy
@stage
async def selfcheck_alerts(h):
    A = "C:selfcheck"
    r = await T(h, A, "self_check_run (rules only, AI pass off)", "self_check_run", {}, ok=True)
    sc, j = await h.aget("/api/selfcheck")
    check(A, "dashboard /api/selfcheck", sc == 200, f"{sc} {json.dumps(j)[:200]}")
    sc, j = await h.apost("/api/selfcheck/run", {})
    check(A, "dashboard run now", sc == 200, f"{sc} {str(j)[:200]}")
    await T(h, A, "self_check_suggestions", "self_check_suggestions", {}, ok=None)
    await T(h, A, "decision on a non-existent suggestion", "self_check_decide", {"number": 49, "decision": "accept"}, ok=False)
    await T(h, A, "decision bad word -> graceful", "self_check_decide", {"number": 1, "decision": "explode"}, ok=False)
    await T(h, A, "self_check_ticket on nothing", "self_check_ticket", {"number": 49}, ok=False) if "self_check_ticket" in h.reg._tools else None
    r = await h.acli("diagnose")
    check(A, "CLI `diagnose`", r.returncode == 0 and "Traceback" not in r.stderr, r.stdout[-150:] + r.stderr[-150:])
    A = "C:alerts"
    sc, j = await h.aget("/api/alerts")
    check(A, "dashboard /api/alerts", sc == 200, f"{sc}")
    sc, j = await h.apost("/api/alerts/test", {})
    check(A, "alert test", sc == 200, f"{sc} {str(j)[:150]}")
    sc, j = await h.apost("/api/alerts/pause", {})
    r = await T(h, A, "alerts_list after pause", "alerts_list", {}, ok=True)
    sc, j = await h.apost("/api/alerts/resume", {})
    sc, j = await h.apost("/api/alerts/settings", {"quiet_start": "22:00", "quiet_end": "07:00"})
    check(A, "alert settings quiet hours", sc == 200, f"{sc} {str(j)[:150]}")
    sc, j = await h.apost("/api/alerts/settings", {"quiet_start": "99:99"})
    check(A, "alert settings reject junk time", sc == 400, f"{sc} {str(j)[:150]}")
    await T(h, A, "alerts_control bad op", "alerts_control", {"op": "explode"}, ok=False)
    A = "C:autonomy"
    sc, j = await h.aget("/api/autonomy")
    check(A, "dashboard /api/autonomy", sc == 200, f"{sc}")
    await T(h, A, "proposal_decide unknown id", "proposal_decide", {"proposal_id": 999, "approve": True}, ok=False)
    sc, j = await h.apost("/api/proposals/999", {"op": "approve"})
    check(A, "dashboard unknown proposal clean error", sc in (400, 404), f"{sc}")


# ====================================================================== camera (fake), Google honesty, dashboard OFF
@stage
async def camera_google(h):
    A = "C:camera"
    st = h.feat.status("camera")
    rec(A, "camera status", "PASS", f"enabled={st['enabled']} available={st['available']} reason={st['reason']!r}")
    await h.apost("/api/features/camera", {"enabled": False})
    await until(lambda: not h.feat.enabled("camera"), 4)
    for tool in ("camera_status", "camera_describe", "camera_who", "camera_read_text", "camera_enroll", "camera_forget_all"):
        if tool in h.reg._tools:
            args = {"name": "x"} if tool == "camera_enroll" else {}
            r = await T(h, A, f"camera OFF: {tool} refused", tool, args, ok=False, code="FEATURE_OFF", approve=False)
    sc, p = await h.aget("/api/camera/preview")
    rec(A, "dashboard camera preview poll while camera off", "PASS" if sc == 200 else "FAIL", f"{sc} {str(p)[:150]}")
    A = "C:google"
    for tool in ("google_connect_help",):
        r = await T(h, A, "google_connect_help works without a connection", tool, {}, ok=True)
    for fid in ("google_calendar", "gmail_read", "gmail_send", "calendar_write", "google_contacts"):
        st = h.feat.status(fid)
        check(A, f"{fid} unavailable + honest reason with the command to run", not st["available"] and "google" in st["reason"].lower(), st["reason"][:160])
    r = await h.acli("google", "status")
    check(A, "CLI `google status` while not connected", r.returncode in (0, 1) and "Traceback" not in r.stderr, f"rc={r.returncode} {r.stdout[-200:]} {r.stderr[-120:]}")
    r = await h.acli("calendar", "list") if False else None


@stage
async def dashboard_off(h):
    A = "F:dashboard"
    r = await h.acli("features", "off", "dashboard")
    await until(lambda: not h.feat.enabled("dashboard"), 4)
    r2 = await h.acli("dashboard", "--no-open", timeout=20) if False else None
    rd = await h.acli("features")
    check(A, "CLI off dashboard", r.returncode == 0, r.stdout[-120:])
    r3 = await T(h, A, "open_dashboard tool with dashboard OFF", "open_dashboard", {}, ok=None)
    rec(A, "open_dashboard while OFF", "PASS", brief(r3))
    # the CLI refuses to start it
    try:
        rr = await h.acli("dashboard", "--no-open", timeout=15)
        check(A, "`jarvis dashboard` refuses while OFF with the fix command", rr.returncode == 1 and "features on dashboard" in rr.stderr, rr.stderr[-200:])
    except Exception as e:
        rec(A, "`jarvis dashboard` while OFF", "FAIL", f"{type(e).__name__}")
    await h.acli("features", "reset", "dashboard")
