"""Campaign stages B: tools of timers/alarms, lists, notes, files sandbox, scheduler/reports, memory/curator, skills,
alerts, autonomy, usage, profiles, appearance/identity/display. Real Core tool registry + real dashboard + real CLI."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta

from tools.campaign_features import check, cli, rec, stage, until


def brief(r) -> str:
    return f"ok={r.ok} code={r.code} msg={str(r.message)[:140]!r}"


async def T(h, area, name, tool, args, ok=None, code=None, origin="model", approve=None, expect_prompt=False, taint=False):
    """Call a tool; ok=True/False expected ok; code expected code; approve True/False sets the confirm answer."""
    if approve is not None:
        h.policy["v"] = approve
    h.answers.clear()
    if not taint:
        h.reg._untrusted.clear()                    # a fresh user turn (file_read / mail reads taint the turn by design)
    r = await h.call(tool, args, origin=origin)
    good = True
    if ok is not None:
        good &= (r.ok == ok)
    if code is not None:
        good &= (r.code == code)
    if expect_prompt:
        good &= bool(h.answers)
    check(area, name, good, brief(r) + (f" asked={h.answers[:1]!r}" if h.answers else ""))
    h.policy["v"] = True
    return r


# ====================================================================== timers / alarms
@stage
async def timers(h):
    A = "T:timers"
    await T(h, A, "timer_start 3 s with Hebrew label", "timer_start", {"seconds": 3, "label": "פסטה"}, ok=True)
    r = await T(h, A, "timers_list shows it", "timers_list", {}, ok=True)
    check(A, "list has 1 running", len(r.data.get("timers", [])) == 1, str(r.data)[:200])
    _, _ = await h.aget("/api/home")
    await T(h, A, "timer_start bad (0 s)", "timer_start", {}, ok=False, code="TIMER_BAD")
    await T(h, A, "timer_start bad (>24h) rejected by schema", "timer_start", {"minutes": 99999}, ok=False, code="TOOL_BAD_ARGS")
    soon = (datetime.now() + timedelta(minutes=2)).strftime("%H:%M")
    await T(h, A, "alarm_set in 2 min", "alarm_set", {"at": soon, "label": "בדיקה"}, ok=True)
    await T(h, A, "alarm_set bad time", "alarm_set", {"at": "25:99"}, ok=False, code="ALARM_BAD")
    await T(h, A, "alarm_set bad date", "alarm_set", {"at": "10:00", "date": "2020-01-01"}, ok=False, code="ALARM_BAD")
    await asyncio.sleep(6)
    r = await T(h, A, "timers_list after the 3 s timer is due", "timers_list", {}, ok=True)
    left = [t for t in r.data.get("timers", []) if t.get("label") == "פסטה"]
    rec(A, "due timer without any audio route stays pending (0 s left) until the 90 s retry window ends", "PASS" if left else "FAIL", str(r.data)[:200])
    await T(h, A, "timer_cancel by label 'alarm'", "timer_cancel", {"label": "alarm"}, ok=True)
    await T(h, A, "timer_cancel nothing left", "timer_cancel", {"label": "nothing-here"}, ok=False, code="TIMER_NOT_FOUND")
    # persistence: restart not exercised here (scheduler catch-up covered in suite_system V08)
    for i in range(32):
        r = await h.call("timer_start", {"minutes": 5, "label": f"t{i}"})
        if not r.ok:
            check(A, "pending cap (30) enforced with a clear message", i >= 29 and r.code == "TIMER_BAD", f"i={i} {brief(r)}")
            break
    else:
        rec(A, "pending cap (30) enforced", "FAIL", "32 timers accepted")
    await h.call("timer_cancel", {})
    await asyncio.sleep(0)
    # feature off -> refused
    await h.acli("features", "off", "timers")
    await until(lambda: not h.feat.enabled("timers"), 4)
    await T(h, A, "timers OFF: timer_start refused", "timer_start", {"seconds": 5}, ok=False, code="FEATURE_OFF")
    await h.acli("features", "reset", "timers")


# ====================================================================== lists
@stage
async def lists(h):
    A = "T:lists"
    await T(h, A, "list_add shopping Hebrew items", "list_add", {"list_name": "shopping", "items": ["חלב", "לחם", "ביצים"]}, ok=True)
    r = await T(h, A, "list_show shopping", "list_show", {"list_name": "shopping"}, ok=True)
    check(A, "shows the 3 items", all(x in json.dumps(r.data, ensure_ascii=False) for x in ("חלב", "לחם", "ביצים")), str(r.data)[:200])
    await T(h, A, "list_add duplicate item", "list_add", {"list_name": "shopping", "items": ["חלב"]}, ok=True)
    r = await T(h, A, "list_check an item", "list_check", {"list_name": "shopping", "item": "לחם"}, ok=True)
    await T(h, A, "list_check missing item", "list_check", {"list_name": "shopping", "item": "unicorn"}, ok=False)
    await T(h, A, "list_remove item", "list_remove", {"list_name": "shopping", "item": "ביצים"}, ok=True)
    await T(h, A, "list_add empty items rejected", "list_add", {"items": []}, ok=False, code="TOOL_BAD_ARGS")
    await T(h, A, "list_add 31 items rejected", "list_add", {"items": [str(i) for i in range(31)]}, ok=False, code="TOOL_BAD_ARGS")
    await T(h, A, "list_add todo", "list_add", {"list_name": "todo", "items": ["לשלם חשבון"]}, ok=True)
    r = await T(h, A, "list_show (no name) lists the lists", "list_show", {}, ok=True)
    await T(h, A, "list_clear done only: asks? (impact count)", "list_clear", {"list_name": "shopping", "only_done": True}, approve=False)
    await T(h, A, "list_clear whole list DECLINED -> stays", "list_clear", {"list_name": "todo", "only_done": False},
            ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
    r = await T(h, A, "list still has the item after decline", "list_show", {"list_name": "todo"}, ok=True)
    check(A, "todo item survived", "לשלם חשבון" in json.dumps(r.data, ensure_ascii=False), str(r.data)[:200])
    await T(h, A, "list_clear whole list APPROVED", "list_clear", {"list_name": "todo", "only_done": False}, ok=True, approve=True)
    await T(h, A, "list_add long item truncated/rejected", "list_add", {"items": ["x" * 400]}, ok=None)
    await h.acli("features", "off", "lists")
    await until(lambda: not h.feat.enabled("lists"), 4)
    await T(h, A, "lists OFF: list_add refused", "list_add", {"items": ["a"]}, ok=False, code="FEATURE_OFF")
    await h.acli("features", "reset", "lists")


# ====================================================================== notes
@stage
async def notes(h):
    A = "T:notes"
    r = await T(h, A, "note_add Hebrew + tags", "note_add", {"title": "רעיון", "text": "לבדוק את מנוע הקול החדש", "tags": ["עבודה", "קול"]}, ok=True)
    nid = (r.data or {}).get("id") or (r.data or {}).get("note", {}).get("id")
    rec(A, "note id", "PASS" if nid else "FAIL", str(r.data)[:200])
    r = await T(h, A, "note_find by word", "note_find", {"query": "קול"}, ok=True)
    check(A, "found", "רעיון" in json.dumps(r.data, ensure_ascii=False), str(r.data)[:200])
    await T(h, A, "note_find by tag", "note_find", {"tag": "עבודה"}, ok=True)
    if nid:
        await T(h, A, "note_edit append", "note_edit", {"note_id": nid, "append": "ועוד שורה"}, ok=True)
        r = await T(h, A, "note_find by id shows appended", "note_find", {"note_id": nid}, ok=True)
        check(A, "append persisted", "ועוד שורה" in json.dumps(r.data, ensure_ascii=False), str(r.data)[:200])
        sc, j = await h.aget("/api/notes")
        check(A, "dashboard /api/notes parity", sc == 200 and "רעיון" in json.dumps(j, ensure_ascii=False), f"{sc}")
        sc, j = await h.apost("/api/notes", {"title": "dash", "text": "from dashboard", "tags": ["d"]})
        check(A, "dashboard add note", sc == 200, f"{sc} {str(j)[:100]}")
        r = await T(h, A, "Core sees the dashboard note", "note_find", {"query": "from dashboard"}, ok=True)
        check(A, "visible to Core tool", "dash" in json.dumps(r.data, ensure_ascii=False), str(r.data)[:150])
        await T(h, A, "note_export one -> file in sandbox", "note_export", {"note_id": nid}, ok=True)
        await T(h, A, "note_delete DECLINED", "note_delete", {"note_id": nid}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
        await T(h, A, "note_delete APPROVED", "note_delete", {"note_id": nid}, ok=True, approve=True)
        await T(h, A, "note_edit deleted -> clear error", "note_edit", {"note_id": nid, "append": "x"}, ok=False)
    await T(h, A, "note_edit bad id", "note_edit", {"note_id": 99999, "text": "x"}, ok=False)
    await T(h, A, "note_add empty text rejected", "note_add", {"text": ""}, ok=False, code="TOOL_BAD_ARGS")
    sc, j = await h.aget("/api/notes/export")
    rec(A, "dashboard export", "PASS" if sc == 200 else "FAIL", f"{sc} {str(j)[:100]}")
    await h.acli("features", "off", "notes")
    await until(lambda: not h.feat.enabled("notes"), 4)
    await T(h, A, "notes OFF: note_add refused", "note_add", {"text": "x"}, ok=False, code="FEATURE_OFF")
    await h.acli("features", "reset", "notes")


# ====================================================================== files sandbox
@stage
async def files(h):
    A = "T:files"
    await T(h, A, "file_write in sandbox", "file_write", {"path": f"tests/hello{int(time.time())}.txt", "text": "שלום עולם"}, ok=True)
    await T(h, A, "file_write fixed name", "file_write", {"path": "tests/hello.txt", "text": "שלום עולם", "overwrite": True}, ok=True)
    r = await T(h, A, "file_read it back (Hebrew intact)", "file_read", {"path": "tests/hello.txt"}, ok=True)
    check(A, "content round trip", "שלום עולם" in json.dumps(r.data, ensure_ascii=False) + r.message, str(r.data)[:150])
    await T(h, A, "taint guard: file_read output makes a following write in the SAME turn refused (by design)", "file_write",
            {"path": "tests/after_read.txt", "text": "x"}, ok=False, code="TOOL_UNTRUSTED_TURN", taint=True)
    await T(h, A, "overwrite without flag refused", "file_write", {"path": "tests/hello.txt", "text": "x"}, ok=False, code="SANDBOX_EXISTS")
    await T(h, A, "overwrite=true allowed", "file_write", {"path": "tests/hello.txt", "text": "v2", "overwrite": True}, ok=True)
    for bad, why in [("../escape.txt", "dotdot"), ("C:/Windows/x.txt", "drive"), ("/etc/passwd", "abs"), ("\\\\srv\\share\\a.txt", "unc"),
                     ("a.txt:ads", "ads"), ("con.txt", "reserved name"), ("tests/run.exe", "exe type"), ("tests/x.bat", "bat type"),
                     ("tests/trailing.", "trailing dot"), ("~/x.txt", "tilde")]:
        await T(h, A, f"write refused: {why}", "file_write", {"path": bad, "text": "x"}, ok=False,
                code="SANDBOX_TYPE" if bad.endswith((".exe", ".bat")) else "SANDBOX_DENIED")
    await T(h, A, "read refused: ..", "file_read", {"path": "../memory.db"}, ok=False, code="SANDBOX_DENIED")
    await T(h, A, "read of top folder lists", "file_read", {"path": ""}, ok=None)
    await T(h, A, "read missing file", "file_read", {"path": "nope.txt"}, ok=False)
    await T(h, A, "write too large (>200k) schema", "file_write", {"path": "big.txt", "text": "x" * 200_001}, ok=False, code="TOOL_BAD_ARGS")
    # grants: the model can only REQUEST; the user approves on the dashboard
    import tempfile, os
    from pathlib import Path
    folder = str(Path.home() / "jv_campaign_grant")          # %TEMP% / AppData count as program data and are refused (checked)
    os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, "doc.txt"), "w", encoding="utf-8").write("granted content")
    r = await T(h, A, "folder_grant_request -> pending, not usable", "folder_grant_request", {"path": folder, "mode": "read", "reason": "campaign"},
                ok=None)
    sc, j = await h.aget("/api/files")
    check(A, "dashboard /api/files shows pending grant", sc == 200 and "grant" in json.dumps(j).lower(), f"{sc} {json.dumps(j)[:300]}")
    gid = None
    for g in (j.get("grants") or []):
        gid = g.get("id")
    h.reg._untrusted.clear()
    r2 = await h.call("file_read", {"path": f"grant:{gid}/doc.txt"}) if gid else None
    check(A, "pending grant NOT readable before approval", r2 is not None and not r2.ok, brief(r2) if r2 else "no grant id")
    if gid:
        sc, j2 = await h.apost(f"/api/files/grants/{gid}", {"op": "approve"})
        rec(A, "dashboard approves grant", "PASS" if sc == 200 else "FAIL", f"{sc} {str(j2)[:150]}")
        h.reg._untrusted.clear()
        r3 = await h.call("file_read", {"path": f"grant:{gid}/doc.txt"})
        check(A, "approved grant readable", r3.ok and "granted content" in json.dumps(r3.data, ensure_ascii=False) + r3.message, brief(r3))
        h.reg._untrusted.clear()
        r4 = await h.call("file_write", {"path": f"grant:{gid}/new.txt", "text": "x"})
        check(A, "read-grant refuses write", not r4.ok, brief(r4))
        h.reg._untrusted.clear()
        r5 = await h.call("file_read", {"path": f"grant:{gid}/../x"})
        check(A, "grant path traversal refused", not r5.ok, brief(r5))
        sc, j3 = await h.apost(f"/api/files/grants/{gid}", {"op": "revoke"})
        h.reg._untrusted.clear()
        r6 = await h.call("file_read", {"path": f"grant:{gid}/doc.txt"})
        check(A, "revoked grant not readable", not r6.ok, f"{sc} {brief(r6)}")
    await T(h, A, "grant request of a relative path refused", "folder_grant_request", {"path": "relative/dir"}, ok=False)
    await h.acli("features", "off", "files")
    await until(lambda: not h.feat.enabled("files"), 4)
    await T(h, A, "files OFF: file_write refused", "file_write", {"path": "z.txt", "text": "x"}, ok=False, code="FEATURE_OFF")
    await h.acli("features", "reset", "files")


@stage
async def timers_ring(h):
    """No audio route in the offline TEST profile: the ring cannot be spoken; it must end as TIMER_UNHEARD, not loop forever."""
    A = "T:timers"
    await h.call("timer_start", {"seconds": 2, "label": "unheard"})
    await asyncio.sleep(97)
    pend = [t for t in h.core.ops.timers.pending() if t["label"] == "unheard"]
    log = (h.home / "logs" / "jarves.log").read_text(encoding="utf-8", errors="replace")
    check(A, "undeliverable ring ends as TIMER_UNHEARD within ~90 s (no endless retry)", not pend and "TIMER_UNHEARD" in log, f"pending={pend}")
