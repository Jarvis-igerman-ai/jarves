"""WP-M3 in the real Core and dashboard (virtual audio, fake TTS, no network): Skills registered behind feature
switches, a reminder delivered by speech/popup, the M3 exit scenario across a real Core restart (a missed daily job
runs ONCE after power-on, is stored and is read back), and every M3 dashboard API over real HTTP."""
import asyncio
import json
import time

from jarves import dashboard, features
from jarves.app import Core
from jarves.config import Config
from jarves.skills.music import FakePlayer
from jarves.userdata import UserDB
from jarves.voice.audio import SimAudio

from conftest import FakeSTT, FakeTTS, open_face
from test_dashboard import get, req

M3_TOOLS = {"schedule_preview", "schedule_approve", "jobs_list", "job_control", "job_delete", "report_find",
            "report_read", "note_add", "note_find", "note_edit", "note_delete", "note_export", "file_write",
            "file_read", "folder_grant_request", "alerts_list", "alerts_control", "proposals_list", "proposal_decide"}


def new_core(cfg, home, tts=None):
    return Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=tts or FakeTTS(),
                console_logs=False, use_real_audio=False)


async def wait_for(pred, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_core_registers_m3_skills_and_features(core):
    names = {t.name for t in core.registry.tools()}
    assert M3_TOOLS <= names
    ids = {f["id"] for f in core.features.list()}
    assert {"scheduler", "reports", "notes", "files", "proactive_alerts", "autonomy"} <= ids
    core.features.set("notes", False, by="user")
    assert "note_add" not in {t.name for t in core.registry.tools()}
    r = await core.registry.call("note_add", {"text": "x"})
    assert r.code == "FEATURE_OFF"
    core.features.set("notes", True, by="user")
    assert (await core.registry.call("note_add", {"title": "קניות", "text": "חלב"})).ok


async def test_reminder_is_spoken_then_shown_on_the_face(core):
    r = await core.registry.call("schedule_preview", {"when": "בעוד 5 דקות", "task": "להתקשר לאמא",
                                                      "kind": "reminder"})
    assert r.ok
    assert (await core.registry.call("schedule_approve", {"job_id": r.data["id"]})).ok
    core.ops.db.x("UPDATE jobs SET next_run=? WHERE id=?", (time.time() - 1, r.data["id"]))
    core.ops.scheduler.poke()
    assert await wait_for(lambda: any("להתקשר לאמא" in t for t in core.tts.spoken))     # no face: TTS speaks it
    face = await open_face(core)
    core.ops.alerts.raise_("test", "דוח מוכן", "אפשר לבקש הקראה", priority="normal")
    core.ops.scheduler.poke()
    m = await face.wait("assistant.text", lambda p: p.get("source") == "alert", timeout=5)
    assert "דוח מוכן" in m["text"]
    await face.ws.close()


async def test_missed_daily_report_runs_once_after_real_restart(home, cfg):
    """M3 exit criterion with two real Core runs on the same data dir (fault injection: the occurrence passed
    while the Core was off)."""
    c = new_core(cfg, home)
    await c.start()
    r = await c.registry.call("schedule_preview", {"when": "כל בוקר ב-8", "task": "תזכורת בוקר", "kind": "reminder",
                                                   "name": "בוקר"})
    jid = r.data["id"]
    await c.registry.call("schedule_approve", {"job_id": jid})
    await c.stop()
    db = UserDB(home / "memory.db")                 # "power off for a day": the 08:00 run is 1 h overdue
    db.x("UPDATE jobs SET next_run=? WHERE id=?", (time.time() - 3600, jid))
    db.close()
    tts = FakeTTS()
    c2 = new_core(cfg, home, tts)
    await c2.start()
    try:
        assert await wait_for(lambda: c2.ops.scheduler.runs(jid) and c2.ops.scheduler.runs(jid)[0]["state"] == "done")
        await asyncio.sleep(0.3)
        runs = c2.ops.scheduler.runs(jid)
        assert len(runs) == 1 and runs[0]["late"] == 1 and runs[0]["trigger"] == "catch_up"
        rd = await c2.registry.call("report_read", {"period": "latest", "query": "בוקר"})
        assert rd.ok and rd.data["late"] is True and rd.data["text"] == "תזכורת בוקר"
        assert c2.ops.scheduler.job(jid)["next_run"] > time.time()
    finally:
        await c2.stop()
    c3 = new_core(cfg, home)                        # a third start: nothing runs again
    await c3.start()
    await asyncio.sleep(0.3)
    assert len(c3.ops.scheduler.runs(jid)) == 1
    await c3.stop()


async def test_dashboard_m3_apis_over_http(home):
    d = dashboard.Dashboard(home, Config()).start()
    try:
        assert "jarves.operations" in d.plugins
        r, body = req(d, "GET", "/static/m3.js")
        assert r.status == 200 and b"Object.assign(PAGES" in body
        r, body = req(d, "POST", "/api/jobs", headers={"X-Jarves": "1"},
                      body={"when": "כל בוקר ב-8", "task": "חדשות AI", "action": "research"})
        assert r.status == 200
        job = json.loads(body)
        assert job["status"] == "draft"
        r, _ = req(d, "POST", "/api/jobs", body={"when": "x", "task": "y"})      # no X-Jarves header: refused
        assert r.status == 403
        r, body = req(d, "POST", f"/api/jobs/{job['id']}", headers={"X-Jarves": "1"}, body={"op": "approve"})
        assert r.status == 200 and json.loads(body)["status"] == "active"
        jobs = get(d, "/api/jobs")
        assert jobs["jobs"][0]["next_run_local"] and jobs["timezone"] == "Asia/Jerusalem"
        r, body = req(d, "POST", "/api/schedule/parse", headers={"X-Jarves": "1"}, body={"when": "every monday at 10"})
        assert json.loads(body)["spec"] == "0 10 * * 1"
        r, body = req(d, "POST", "/api/notes", headers={"X-Jarves": "1"}, body={"title": "a", "text": "b"})
        assert r.status == 200
        assert get(d, "/api/notes")["notes"][0]["title"] == "a"
        assert get(d, "/api/files")["path"] == "."
        r, _ = req(d, "GET", "/api/files/read?path=..%2Fmemory.db")
        assert r.status == 403
        assert get(d, "/api/alerts")["settings"]["alerts.quiet_start"] == "22:00"
        assert get(d, "/api/autonomy")["level"] == "suggest_and_ask"
        r, body = req(d, "POST", "/api/autonomy", headers={"X-Jarves": "1"}, body={"level": "approved_low_risk"})
        assert r.status == 200
        plat = get(d, "/api/m3/platforms")["features"]
        assert {p["linux_arm64_pi"]["status"] for p in plat} <= {"implemented_unverified", "planned"}
        assert all(p["linux_arm64_pi"]["blocker"] and p["linux_arm64_pi"]["roadmap_issue"].startswith(
            "https://github.com/igalgerman/JARVIS/issues/") for p in plat)
        feats = {f["id"] for f in get(d, "/api/features")["features"]}
        assert "scheduler" in feats
    finally:
        d.stop()
    assert features.CATALOG["scheduler"].skills == ["scheduler"]
