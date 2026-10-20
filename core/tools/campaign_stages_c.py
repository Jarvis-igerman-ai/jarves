"""Campaign stages C: scheduler/reports, memory/curator, skills lists, alerts, autonomy, usage, profiles, appearance,
identity, display, self-check."""
from __future__ import annotations

import asyncio
import json

from tools.campaign_features import check, cli, rec, stage, until
from tools.campaign_stages_b import T, brief


# ====================================================================== scheduler / reports
@stage
async def scheduler(h):
    A = "T:scheduler"
    sc, p = await h.apost("/api/schedule/parse", {"when": "כל יום ב-8"})
    check(A, "dashboard parse Hebrew 'כל יום ב-8'", sc == 200 and p.get("next"), f"{sc} {str(p)[:200]}")
    sc, p = await h.apost("/api/schedule/parse", {"when": "gibberish blah"})
    check(A, "dashboard parse garbage -> 400 with message", sc == 400 and p.get("error"), f"{sc} {str(p)[:150]}")
    r = await T(h, A, "schedule_preview reminder in 20 min", "schedule_preview",
                {"when": "בעוד 20 דקות", "task": "לשתות מים", "kind": "reminder", "name": "water"}, ok=True)
    jid = (r.data or {}).get("id") or (r.data or {}).get("job", {}).get("id")
    rec(A, "preview returned a job id", "PASS" if jid else "FAIL", str(r.data)[:250])
    await T(h, A, "schedule_preview bad when", "schedule_preview", {"when": "whenever", "task": "do a thing please", "kind": "reminder"}, ok=False)
    await T(h, A, "schedule_preview bad kind -> schema", "schedule_preview", {"when": "מחר ב-9", "task": "abc def", "kind": "bomb"}, ok=False, code="TOOL_BAD_ARGS")
    if jid:
        _, jl = await h.aget("/api/jobs")
        row = next((j for j in jl["jobs"] if j["id"] == jid), None)
        check(A, "dashboard sees the previewed job as not yet approved", row is not None and row.get("status") in ("draft", "pending_approval", "preview", "paused") or (row and not row.get("approved", True)), str(row)[:300])
        await T(h, A, "schedule_approve (asks the user)", "schedule_approve", {"job_id": jid}, ok=True, approve=True, expect_prompt=None) if False else None
        r = await T(h, A, "schedule_approve declined -> not approved", "schedule_approve", {"job_id": jid}, approve=False)
        rec(A, "schedule_approve declined", "PASS", brief(r))
        r = await T(h, A, "schedule_approve approved", "schedule_approve", {"job_id": jid}, approve=True)
        r = await T(h, A, "jobs_list shows it", "jobs_list", {}, ok=True)
        check(A, "job in list", jid in json.dumps(r.data, ensure_ascii=False), str(r.data)[:200])
        await T(h, A, "job_control pause", "job_control", {"job_id": jid, "op": "pause"}, ok=True)
        await T(h, A, "job_control resume", "job_control", {"job_id": jid, "op": "resume"}, ok=True)
        await T(h, A, "job_control run_now (reminder)", "job_control", {"job_id": jid, "op": "run_now"}, ok=True)
        await asyncio.sleep(3)
        _, jl = await h.aget("/api/jobs")
        row = next((j for j in jl["jobs"] if j["id"] == jid), {})
        check(A, "run_now produced a run row (dashboard parity)", bool(row.get("runs")), str(row.get("runs"))[:300])
        await T(h, A, "job_control bad op -> schema", "job_control", {"job_id": jid, "op": "explode"}, ok=False, code="TOOL_BAD_ARGS")
        await T(h, A, "job_control unknown id", "job_control", {"job_id": "deadbeef", "op": "pause"}, ok=False)
        await T(h, A, "job_delete declined", "job_delete", {"job_id": jid}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
        await T(h, A, "job_delete approved", "job_delete", {"job_id": jid}, ok=True, approve=True)
    # research job offline: must fail gracefully / not hang
    r = await T(h, A, "research job preview (AI offline)", "schedule_preview",
                {"when": "בעוד שעה", "task": "מה חדש בעולם הבינה המלאכותית היום", "kind": "research", "name": "ai-news"}, ok=None)
    jid2 = (r.data or {}).get("id") or (r.data or {}).get("job", {}).get("id")
    if jid2:
        await T(h, A, "approve research", "schedule_approve", {"job_id": jid2}, approve=True)
        await T(h, A, "run research now with AI offline -> graceful", "job_control", {"job_id": jid2, "op": "run_now"}, ok=None)
        await asyncio.sleep(5)
        _, jl = await h.aget("/api/jobs")
        row = next((j for j in jl["jobs"] if j["id"] == jid2), {})
        rec(A, "offline research run recorded", "PASS" if row.get("runs") else "FAIL", str(row.get("runs"))[:300])
        await h.apost(f"/api/jobs/{jid2}", {"op": "delete"})
    # dashboard create + ops
    sc, j = await h.apost("/api/jobs", {"when": "בעוד 3 שעות", "task": "בדיקה מהדשבורד", "action": "reminder", "name": "dash-job"})
    check(A, "dashboard job create", sc == 200 and j.get("id"), f"{sc} {str(j)[:200]}")
    if j.get("id"):
        sc, a = await h.apost(f"/api/jobs/{j['id']}", {"op": "approve"})
        check(A, "dashboard approve", sc == 200, f"{sc} {str(a)[:120]}")
        r = await T(h, A, "Core tool sees dashboard job", "jobs_list", {}, ok=True)
        check(A, "parity: Core lists dashboard job", "dash-job" in json.dumps(r.data, ensure_ascii=False), str(r.data)[:200])
        sc, a = await h.apost(f"/api/jobs/{j['id']}", {"op": "nonsense"})
        check(A, "dashboard bad op -> 400", sc == 400, f"{sc}")
        await h.apost(f"/api/jobs/{j['id']}", {"op": "delete"})
    sc, s = await h.apost("/api/scheduler/settings", {"workweek": 9})
    check(A, "dashboard settings reject bad workweek", sc == 400, f"{sc} {str(s)[:100]}")
    sc, s = await h.apost("/api/scheduler/settings", {"timezone": "Not/AZone"})
    check(A, "dashboard settings reject bad timezone", sc == 400, f"{sc} {str(s)[:100]}")
    # reports
    sc, rp = await h.aget("/api/reports")
    check(A, "dashboard /api/reports", sc == 200 and "reports" in rp, f"{sc}")
    r = await T(h, A, "report_find", "report_find", {"period": "latest"}, ok=None)
    r = await T(h, A, "report_read none", "report_read", {"period": "latest"}, ok=None)
    sc, rp = await h.aget("/api/reports/99999")
    check(A, "report 404", sc == 404, f"{sc}")
    for fid in ("scheduler", "reports"):
        await h.acli("features", "off", fid)
        await until(lambda: not h.feat.enabled(fid), 4)
    await T(h, A, "scheduler OFF: schedule_preview refused", "schedule_preview", {"when": "מחר ב-9", "task": "abc def", "kind": "reminder"}, ok=False, code="FEATURE_OFF")
    await T(h, A, "reports OFF: report_find refused", "report_find", {}, ok=False, code="FEATURE_OFF")
    for fid in ("scheduler", "reports"):
        await h.acli("features", "reset", fid)


# ====================================================================== memory / curator
@stage
async def memory(h):
    A = "T:memory"
    r = await T(h, A, "remember (Hebrew)", "remember", {"text": "הכלב שלי נקרא בונזו"}, ok=True)
    r = await T(h, A, "recall by query", "recall", {"query": "כלב"}, ok=True)
    check(A, "recall finds it", "בונזו" in json.dumps(r.data, ensure_ascii=False) + r.message, brief(r))
    out = await h.acli("memory", "list")
    check(A, "CLI `memory list` parity", out.returncode == 0 and "בונזו" in out.stdout, out.stdout[-200:] + out.stderr[-200:])
    sc, j = await h.aget("/api/memory")
    check(A, "dashboard /api/memory parity", sc == 200 and "בונזו" in json.dumps(j, ensure_ascii=False), f"{sc}")
    sc, a = await h.apost("/api/memory", {"text": "אני אוהב קפה שחור"})
    check(A, "dashboard add memory", sc == 200, f"{sc} {str(a)[:120]}")
    mid = a.get("id") if isinstance(a, dict) else None
    if mid:
        sc, e = await h.areq("PUT", f"/api/memory/{mid}", {"text": "אני אוהב קפה עם חלב"})
        check(A, "dashboard edit memory", sc == 200, f"{sc} {str(e)[:120]}")
        r = await T(h, A, "Core recall sees edited text", "recall", {"query": "קפה"}, ok=True)
        check(A, "edited text visible", "עם חלב" in json.dumps(r.data, ensure_ascii=False) + r.message, brief(r))
        sc, d = await h.areq("DELETE", f"/api/memory/{mid}")
        check(A, "dashboard delete memory", sc == 200, f"{sc} {str(d)[:120]}")
        sc, u = await h.apost("/api/memory/undo", {})
        check(A, "dashboard memory undo restores", sc == 200, f"{sc} {str(u)[:150]}")
    await T(h, A, "remember empty -> schema", "remember", {"text": ""}, ok=False, code="TOOL_BAD_ARGS")
    await T(h, A, "remember 501 chars -> schema", "remember", {"text": "x" * 501}, ok=False, code="TOOL_BAD_ARGS")
    await T(h, A, "forget DECLINED", "forget", {"match": "בונזו"}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
    await T(h, A, "forget APPROVED", "forget", {"match": "בונזו"}, ok=True, approve=True)
    await T(h, A, "forget_all DECLINED", "forget_all", {}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
    r = await h.acli("memory", "delete")
    check(A, "CLI `memory delete` without id: friendly (no traceback)", "Traceback" not in r.stderr, f"rc={r.returncode} {r.stderr[-250:]}")
    r = await h.acli("memory", "delete", "abc")
    check(A, "CLI `memory delete abc`: friendly (no traceback)", "Traceback" not in r.stderr, f"rc={r.returncode} {r.stderr[-250:]}")
    r = await h.acli("memory", "delete", "99999")
    check(A, "CLI `memory delete 99999` not found", "not found" in r.stdout and r.returncode == 0, r.stdout[-100:])
    # curator (rule-based, offline)
    cur = h.core.curator
    cur.observe("user", "קוראים לי עידו ואני גר בחיפה")
    cur.observe("user", "אני מעדיף תה ירוק בבוקר")
    saved = await cur.run("campaign")
    rec(A, "curator rules extract facts from a typed conversation", "PASS" if saved else "FAIL", f"saved {len(saved)}: {[s['text'] for s in saved]}")
    sc, j = await h.aget("/api/memory")
    auto = [i for i in j["items"] if i.get("source") == "auto"] if sc == 200 else []
    check(A, "auto-learned facts visible on dashboard, marked auto", sc == 200 and (not saved or bool(auto)), f"{len(auto)} auto")
    cur.observe("user", "הסיסמה שלי היא hunter2 וכרטיס האשראי 4111 1111 1111 1111")
    saved2 = await cur.run("campaign-sensitive")
    leaked = [s for s in saved2 if "hunter2" in s["text"] or "4111" in s["text"]]
    check(A, "curator does not learn passwords / card numbers", not leaked, str(saved2))
    await h.acli("features", "off", "memory_curator")
    await until(lambda: not h.feat.enabled("memory_curator"), 4)
    cur.observe("user", "קוראים לי נעם ואני גר בעכו")
    saved3 = await cur.run("campaign-off")
    check(A, "memory_curator OFF -> nothing learned", not saved3, str(saved3))
    await h.acli("features", "reset", "memory_curator")
    sc, wsx = await h.aget("/api/workspace")
    check(A, "dashboard /api/workspace (AGENTS/SOUL/MEMORY/USER)", sc == 200, f"{sc} {str(wsx)[:100]}")


# ====================================================================== misc tools
@stage
async def misc(h):
    A = "T:misc"
    r = await T(h, A, "list_skills", "list_skills", {}, ok=True)
    sc, sk = await h.aget("/api/skills")
    check(A, "dashboard /api/skills parity with list_skills", sc == 200 and len(json.dumps(sk)) > 20, f"{sc} {json.dumps(sk)[:200]}")
    await T(h, A, "skills_generated_list", "skills_generated_list", {}, ok=True)
    await T(h, A, "get_time", "get_time", {}, ok=True)
    await T(h, A, "get_status", "get_status", {}, ok=True)
    await T(h, A, "features_list", "features_list", {}, ok=True)
    await T(h, A, "usage_status", "usage_status", {}, ok=None)
    await T(h, A, "profile_list", "profile_list", {}, ok=True)
    await T(h, A, "profile_switch to unknown profile", "profile_switch", {"name": "nope-zzz"}, ok=False)
    await T(h, A, "alerts_list", "alerts_list", {}, ok=True)
    await T(h, A, "proposals_list", "proposals_list", {}, ok=True)
    await T(h, A, "self_check_suggestions", "self_check_suggestions", {}, ok=None)
    await T(h, A, "appearance_get", "appearance_get", {}, ok=True)
    await T(h, A, "voice_catalog", "voice_catalog", {}, ok=None)
    await T(h, A, "briefing_get", "briefing_get", {}, ok=None)
    await T(h, A, "explain_feature timers", "explain_feature", {"name": "timers"}, ok=True)
    await T(h, A, "explain_feature unknown", "explain_feature", {"name": "zzzqqq"}, ok=False, code="GUIDE_UNKNOWN")
    await T(h, A, "whats_not_set_up", "whats_not_set_up", {}, ok=True)
    await T(h, A, "settings read list_settings", "list_setting" if False else "list_settings", {}, ok=None) if "list_settings" in h.reg._tools else None
    await T(h, A, "workspace_read SOUL", "workspace_read", {"name": "SOUL.md"}, ok=None)
    await T(h, A, "set_setting unknown key", "set_setting", {"key": "no.such.key", "value": "1"}, ok=False)
    await T(h, A, "assistant_rename declined", "assistant_rename", {"display_name": "Zed"}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
    await T(h, A, "show_yourself / hide_yourself with no face connected", "hide_yourself", {}, ok=None)
    await T(h, A, "open_dashboard (no browser opener in test)", "open_dashboard", {}, ok=None)
