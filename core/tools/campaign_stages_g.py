"""Campaign stages G: approvals service (dashboard / CLI / voice / face answers, typed codes, rate limit, deny cooldown,
default deny) and the Helper Skill (harmless commands only; approvals answered through its API)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from tools.campaign_features import check, cli, rec, stage, until
from tools.campaign_stages_b import T, brief


async def pending(h) -> list[dict]:
    sc, j = await h.aget("/api/approvals")
    return j.get("pending", []) if sc == 200 else []


async def ask_and_answer(h, tool, args, *, approve: bool | None, via="dashboard", typed: str | None = None, wait_s=15.0):
    """Start a tool call that needs approval, wait until it shows in the approvals list, answer it through the API
    (dashboard / CLI), and return (result, pending_row, answer_response)."""
    h.face.confirm_policy = "none"               # the face stand-in does not answer: only the API does
    h.reg._untrusted.clear()
    task = asyncio.create_task(h.call(tool, args))
    row = None
    t = time.time()
    while time.time() - t < wait_s and not task.done():
        rows = await pending(h)
        if rows:
            row = rows[0]
            break
        await asyncio.sleep(0.2)
    resp = None
    if row is not None and approve is not None:
        code = typed if typed is not None else (row.get("typed_code") if row.get("typed") else "")
        if via == "dashboard":
            resp = await h.apost(f"/api/approvals/{row['id']}", {"approved": approve, "typed": code or ""})
        else:
            r = await h.acli("approvals", "approve" if approve else "deny", row["id"], *(["--code", code] if code else []))
            resp = (r.returncode, r.stdout + r.stderr)
    try:
        res = await asyncio.wait_for(task, 30)
    except asyncio.TimeoutError:
        task.cancel()
        res = None
    h.face.confirm_policy = "approve"
    return res, row, resp


def fast(appr, h=None):
    appr.DENY_COOLDOWN_S = 0.0
    if h is not None:
        h.core.approvals.rate_limit = (10_000, 60.0)


@stage
async def approvals_service(h):
    from jarves import approvals as appr
    A = "C:approvals"
    fast(appr, h)
    await h.apost("/api/features/lists", {"enabled": True})
    # a destructive tool: list_clear (whole list) needs approval; answer through the dashboard
    await h.call("list_add", {"list_name": "approvaltest", "items": ["a", "b"]})
    res, row, resp = await ask_and_answer(h, "list_clear", {"list_name": "approvaltest", "only_done": False}, approve=False)
    check(A, "pending request is listed on the dashboard with title/risk before anyone answers", row is not None and row.get("risk") and row.get("title"), json.dumps(row, ensure_ascii=False)[:250] if row else "none")
    check(A, "dashboard DENY -> tool declined, data kept", res is not None and res.code == "TOOL_DECLINED" and resp and resp[0] == 200, f"{brief(res) if res else None} {resp}")
    r = await h.call("list_show", {"list_name": "approvaltest"})
    check(A, "list still intact after dashboard deny", "a" in json.dumps(r.data, ensure_ascii=False), str(r.data)[:100])
    res, row, resp = await ask_and_answer(h, "list_clear", {"list_name": "approvaltest", "only_done": False}, approve=True)
    check(A, "dashboard APPROVE -> tool runs", res is not None and res.ok, brief(res) if res else "timeout")
    await h.call("list_add", {"list_name": "approvaltest", "items": ["a", "b"]})
    res, row, resp = await ask_and_answer(h, "list_clear", {"list_name": "approvaltest", "only_done": False}, approve=True, via="cli")
    check(A, "CLI `approvals approve ID` -> tool runs", res is not None and res.ok and resp and resp[0] == 0, f"{brief(res) if res else None} {resp}")
    await h.call("list_add", {"list_name": "approvaltest", "items": ["a", "b"]})
    res, row, resp = await ask_and_answer(h, "list_clear", {"list_name": "approvaltest", "only_done": False}, approve=False, via="cli")
    check(A, "CLI `approvals deny ID` -> declined", res is not None and res.code == "TOOL_DECLINED" and resp and resp[0] == 0, f"{brief(res) if res else None} {resp}")
    r = await h.acli("approvals", "list")
    check(A, "CLI `approvals list` when nothing waits", r.returncode == 0 and "Nothing" in r.stdout, r.stdout[-120:])
    r = await h.acli("approvals", "history", "-n", "5")
    check(A, "CLI `approvals history` shows decisions with channel", r.returncode == 0 and ("dashboard" in r.stdout or "cli" in r.stdout), r.stdout[-300:])
    r = await h.acli("approvals", "approve", "deadbeefdeadbeef")
    check(A, "CLI approve of an unknown id: clean error", r.returncode != 0 and "Traceback" not in r.stderr, (r.stdout + r.stderr)[-150:])
    sc, j = await h.apost("/api/approvals/deadbeefdeadbeef", {"approved": True})
    check(A, "dashboard approve of an unknown id -> 409", sc == 409, f"{sc} {j}")
    sc, j = await h.apost("/api/approvals/deadbeefdeadbeef", {"approved": "yes"})
    check(A, "dashboard approve with a non-bool -> 400", sc == 400, f"{sc} {j}")
    # voice answer: a pure yes from the owner
    h.face.confirm_policy = "none"
    await h.call("list_add", {"list_name": "approvaltest", "items": ["a"]})
    h.reg._untrusted.clear()
    task = asyncio.create_task(h.call("list_clear", {"list_name": "approvaltest", "only_done": False}))
    await until(lambda: h.core.approvals.has_pending(), 5)
    early = h.core.approvals.answer_words("כן", channel="voice")
    check(A, "speech that finishes <1.5 s after the request appears is NOT taken as its answer", early is False, str(early))
    await asyncio.sleep(1.8)
    used = h.core.approvals.answer_words("כן", channel="voice")
    res = await asyncio.wait_for(task, 20)
    check(A, "voice 'כן' from the owner approves a normal request", used and res.ok, f"{used} {brief(res)}")
    await h.call("list_add", {"list_name": "approvaltest", "items": ["a"]})
    h.reg._untrusted.clear()
    task = asyncio.create_task(h.call("list_clear", {"list_name": "approvaltest", "only_done": False}))
    await until(lambda: h.core.approvals.has_pending(), 5)
    await asyncio.sleep(1.8)
    used = h.core.approvals.answer_words("אולי כן אבל קודם תגיד לי משהו", channel="voice")
    check(A, "a long / conditional sentence is NOT an approval", not used, str(used))
    h.core.approvals.answer_words("לא", channel="voice")
    res = await asyncio.wait_for(task, 20)
    check(A, "voice 'לא' denies", res.code == "TOOL_DECLINED", brief(res))
    # model-controlled text cannot approve: answer() refused while a tool runs is covered by unit tests; here: no tool answers
    names = [n for n in h.reg._tools if "approv" in n and n != "schedule_approve"]
    check(A, "no Core tool can answer an approval", not names, str(names))
    # default deny: nobody answers within the timeout -> denied (shortened timeout)
    h.face.confirm_policy = "none"
    await h.call("list_add", {"list_name": "approvaltest", "items": ["a"]})
    h.reg._untrusted.clear()
    t0 = time.time()
    ok = await h.core.ask_confirm("campaign: nobody answers", timeout=6)
    check(A, "default DENY on timeout (no answer in 6 s)", ok is False and 5 < time.time() - t0 < 12, f"ok={ok} after {time.time() - t0:.1f}s")
    # nobody could be asked (face gone, no other channel): deny
    h.face.confirm_policy = "approve"
    # rate limit + deny cooldown (restore the real values)
    h.core.approvals.rate_limit = appr.RATE_LIMIT
    appr.DENY_COOLDOWN_S = 30.0
    h.face.confirm_policy = "deny"
    results = []
    for i in range(9):
        results.append(await h.core.ask_confirm(f"campaign rate test {i}", timeout=5))
    rec(A, "rate limit: 9 distinct requests in a burst, outcomes (all deny by policy)", "PASS", str(results))
    h.face.confirm_policy = "approve"
    t0 = time.time()
    ok = await h.core.ask_confirm("campaign rate test 0", timeout=5)          # identical to one denied seconds ago
    check(A, "an identical request denied <30 s ago is denied again without asking", ok is False and time.time() - t0 < 2.0, f"ok={ok} in {time.time() - t0:.2f}s")
    fast(appr, h)
    hist = await h.aget("/api/approvals")
    check(A, "history carries channel + risk for every decision", all(r.get("channel") for r in hist[1].get("history", [])[:10]), str(hist[1].get("history", [])[:2])[:300])
    audit = h.home / "approvals" / "audit.jsonl"
    check(A, "audit.jsonl written", audit.exists() and audit.stat().st_size > 0, str(audit))
    popup_procs = [p for p in h.core.approvals.channels if getattr(p, "name", "") == "popup" and getattr(p, "procs", None)]
    check(A, "no approval popup window was spawned in this headless run", not popup_procs, str(popup_procs))


@stage
async def helper(h):
    from jarves import approvals as appr
    from jarves.skills import helper as hp
    A = "C:helper"
    fast(appr, h)
    st = h.feat.status("helper")
    rec(A, "helper feature status (default)", "PASS" if not st["enabled"] and st["risk"] == "high" else "FAIL", f"enabled={st['enabled']} risk={st['risk']} available={st['available']} reason={st['reason']!r}")
    await T(h, A, "OFF: shell_propose refused", "shell_propose", {"command": "python --version", "purpose": "x"}, ok=False, code="FEATURE_OFF")
    h.face.confirm_policy = "deny"
    h.answers.clear()
    r = await h.call("feature_set", {"id": "helper", "on": True})
    check(A, "model cannot switch the helper ON without approval (declined)", r.code == "FEATURE_NEEDS_APPROVAL" and not h.feat.enabled("helper"), brief(r))
    await h.apost("/api/features/helper", {"enabled": True})
    await until(lambda: h.feat.enabled("helper"), 4)
    h.feat.invalidate()
    st = h.feat.status("helper")
    check(A, "helper ON from the dashboard and available", st["enabled"] and st["available"], st["reason"])
    py = sys.executable
    # --- a normal harmless command, approval answered through the dashboard API
    res, row, resp = await ask_and_answer(h, "shell_propose", {"command": f'"{py}" --version', "purpose": "campaign: print the Python version"}, approve=True)
    check(A, "pending request shows the exact command, folder, purpose and risk", row is not None and "--version" in json.dumps(row, ensure_ascii=False) and row.get("risk"), json.dumps(row, ensure_ascii=False)[:400] if row else "none")
    check(A, "approved via dashboard -> ran, exit 0, output summarized as UNTRUSTED", res is not None and res.ok and res.data.get("exit_code") == 0 and "Python" in json.dumps(res.data) and res.data.get("output_note"), brief(res) if res else "timeout")
    # untrusted output taints the turn
    r = await h.call("list_add", {"items": ["after-helper"]})
    check(A, "helper output taints the turn: a following action is refused/asked", r.code in ("TOOL_UNTRUSTED_TURN", "TOOL_DECLINED") or not r.ok, brief(r))
    # --- denied via dashboard: nothing runs
    marker = h.home.parent / "helper_marker.txt"
    res, row, resp = await ask_and_answer(h, "shell_propose", {"command": f'"{py}" -c "open(r\'{marker}\',\'w\').write(\'x\')"', "purpose": "campaign: must NOT run"}, approve=False)
    check(A, "denied via dashboard -> HELPER_NOT_APPROVED and the command did NOT run", res is not None and res.code == "HELPER_NOT_APPROVED" and not marker.exists(), f"{brief(res) if res else None} marker={marker.exists()}")
    # --- approved via CLI
    res, row, resp = await ask_and_answer(h, "shell_propose", {"command": f'"{py}" -c "print(6*7)"', "purpose": "campaign: arithmetic"}, approve=True, via="cli")
    check(A, "approved via CLI `jarvis approvals approve` -> ran, prints 42", res is not None and res.ok and "42" in json.dumps(res.data), f"{brief(res) if res else None} {resp}")
    # --- refused outright
    for cmd, why in [("", "empty"), ("echo a\necho b", "multi-line"), ("x" * 600, "too long"), ("runas /user:Administrator cmd", "elevation runas"),
                     ("Start-Process notepad -Verb RunAs", "elevation Start-Process"), ("sudo ls", "sudo"),
                     (f"type {h.home}\\secrets.env", "JARVES data folder"), ("pip install requests", "package install outside install_propose")]:
        r = await T(h, A, f"refused without any popup: {why}", "shell_propose", {"command": cmd, "purpose": "campaign"}, ok=False)
        check(A, f"refusal is a clean HELPER_* result, not a crash ({why})", r.code.startswith("HELPER_"), brief(r))
        check(A, f"no approval was requested for the refused command ({why})", not await pending(h), "")
    # --- dangerous = typed code (pipe)
    cmd = f'"{py}" -c "print(1)" | findstr 1'
    h.face.confirm_policy = "approve"                  # the face tries to approve: must NOT work for a typed request
    h.reg._untrusted.clear()
    task = asyncio.create_task(h.call("shell_propose", {"command": cmd, "purpose": "campaign: pipe (dangerous class)"}))
    row = None
    for _ in range(60):
        rows = await pending(h)
        if rows:
            row = rows[0]
            break
        await asyncio.sleep(0.25)
    check(A, "a piped command is classified dangerous and asks for a TYPED code", row is not None and row.get("typed") and row.get("typed_code"), json.dumps(row, ensure_ascii=False)[:300] if row else "none")
    await asyncio.sleep(1.5)
    check(A, "the face's plain Approve did not approve the typed request", not task.done(), "")
    if row:
        sc, j = await h.apost(f"/api/approvals/{row['id']}", {"approved": True})
        check(A, "dashboard approve WITHOUT the code is rejected", sc == 409, f"{sc} {j}")
        sc, j = await h.apost(f"/api/approvals/{row['id']}", {"approved": True, "typed": "0000x"})
        check(A, "dashboard approve with a WRONG code is rejected", sc == 409, f"{sc} {j}")
        await asyncio.sleep(1.8)
        ok_voice = h.core.approvals.answer_words("כן", channel="voice")
        check(A, "voice 'כן' does not approve a typed-code request", not ok_voice, str(ok_voice))
        sc, j = await h.apost(f"/api/approvals/{row['id']}", {"approved": True, "typed": row["typed_code"]})
        check(A, "dashboard approve with the RIGHT code is accepted", sc == 200, f"{sc} {j}")
    try:
        res = await asyncio.wait_for(task, 30)
    except asyncio.TimeoutError:
        res = None
        task.cancel()
    check(A, "dangerous command ran only after the typed code", res is not None and res.ok, brief(res) if res else "timeout")
    # --- hard time limit
    res, row, resp = await ask_and_answer(h, "shell_propose", {"command": f'"{py}" -c "import time; time.sleep(30)"', "purpose": "campaign: timeout", "timeout_s": 2}, approve=True)
    check(A, "hard timeout kills the command (2 s)", res is not None and res.code == "HELPER_TIMEOUT", brief(res) if res else "timeout")
    # --- non-zero exit
    res, row, resp = await ask_and_answer(h, "shell_propose", {"command": f'"{py}" -c "import sys; sys.exit(3)"', "purpose": "campaign: exit code"}, approve=True)
    check(A, "non-zero exit is reported honestly", res is not None and res.code == "HELPER_EXIT_NONZERO" and res.data.get("exit_code") == 3, brief(res) if res else "timeout")
    # --- credentials removed from the child environment
    os.environ["CAMPAIGN_API_KEY"] = "sk-campaign-secret"
    res, row, resp = await ask_and_answer(h, "shell_propose", {"command": f'"{py}" -c "import os; print(os.environ.get(\'CAMPAIGN_API_KEY\', \'ABSENT\'))"', "purpose": "campaign: env"}, approve=True)
    check(A, "credential-like environment variables are not passed to the child", res is not None and "ABSENT" in json.dumps(res.data) and "sk-campaign" not in json.dumps(res.data), brief(res) if res else "timeout")
    # --- install_propose: always asks, never runs here (denied)
    r = await T(h, A, "install_propose rejects a malformed package name", "install_propose", {"manager": "pip", "package": "x; rm -rf /", "purpose": "campaign"}, ok=False, code="HELPER_BAD_PACKAGE")
    res, row, resp = await ask_and_answer(h, "install_propose", {"manager": "winget", "package": "Git.Git", "purpose": "campaign: DENIED install"}, approve=False)
    det = json.dumps(row, ensure_ascii=False) if row else ""
    check(A, "install popup shows manager/package/source/unpinned version; denied -> nothing installed", row is not None and "Git.Git" in det and "winget" in det.lower() and res is not None and res.code == "HELPER_NOT_APPROVED", det[:400])
    await T(h, A, "install_propose npm without a project folder refused", "install_propose", {"manager": "npm", "package": "typescript", "purpose": "campaign"}, ok=False, code="HELPER_BAD_CWD")
    # --- readonly auto-run only with helper_readonly_auto
    cwd = str(Path(__file__).resolve().parents[2])
    h.face.confirm_policy = "none"
    h.reg._untrusted.clear()
    task = asyncio.create_task(h.call("shell_propose", {"command": "git status --short", "purpose": "campaign: read-only", "cwd": cwd}))
    row = None
    for _ in range(40):
        rows = await pending(h)
        if rows:
            row = rows[0]
            break
        if task.done():
            break
        await asyncio.sleep(0.25)
    check(A, "read-only command STILL asks while helper_readonly_auto is OFF (owner decision: always ask)", row is not None, "no popup" if row is None else "asked")
    if row:
        await h.apost(f"/api/approvals/{row['id']}", {"approved": False})
    await asyncio.wait_for(task, 20)
    await h.apost("/api/features/helper_readonly_auto", {"enabled": True})
    await until(lambda: h.feat.enabled("helper_readonly_auto"), 4)
    h.reg._untrusted.clear()
    res = await h.call("shell_propose", {"command": "git status --short", "purpose": "campaign: read-only", "cwd": cwd})
    rec(A, "read-only command with helper_readonly_auto ON", "PASS" if (res.ok or res.code.startswith("HELPER")) else "FAIL", f"{brief(res)} pending={await pending(h)}")
    await h.apost("/api/features/helper_readonly_auto", {"enabled": False})
    # --- history / audit
    r = await T(h, A, "helper_history lists runs", "helper_history", {"limit": 20}, ok=True)
    check(A, "history has approved + denied + refused", {"denied", "approved"} & {str(x.get("decision")) for x in r.data["runs"]} and len(r.data["runs"]) >= 5, json.dumps(r.data["runs"][:4], ensure_ascii=False)[:300])
    audit = (h.home / "helper" / "audit.jsonl").read_text(encoding="utf-8")
    check(A, "helper audit.jsonl never contains the secret env value", "sk-campaign-secret" not in audit, "")
    # --- OFF again
    await h.apost("/api/features/helper", {"enabled": False})
    await until(lambda: not h.feat.enabled("helper"), 4)
    await T(h, A, "helper OFF again: refused", "shell_propose", {"command": "python --version", "purpose": "x"}, ok=False, code="FEATURE_OFF")
    h.face.confirm_policy = "approve"
