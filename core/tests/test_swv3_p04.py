"""SW-V3 P0.4 regressions found by the installed-system QA (docs/reviews/SWV3_P04_BUG_TABLE.md)."""
from __future__ import annotations

import os
import subprocess
import sys


def _cli(tmp_path, *args):
    env = {**os.environ, "JARVES_HOME": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    env.pop("OPENAI_API_KEY", None)
    return subprocess.run([sys.executable, "-m", "jarves", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=120)


async def test_q04_dashboard_run_now_wakes_the_core_scheduler_at_once(core):
    """Q04: the dashboard opened from the Core had its own Scheduler, so "Run now" / approve only took effect at the
    Core's next 30 s poll (measured 26.1 s in the live QA run). Now the Core's scheduler is shared and poke() is
    thread-safe (the dashboard is an HTTP thread)."""
    import asyncio
    import json
    import time

    from test_dashboard import req
    url = await core.open_dashboard()
    d = core._dashboard
    assert url and d.ctx._scheduler is core.ops.scheduler
    await asyncio.sleep(0.3)                      # the scheduler loop is now in its long (30 s) wait
    h = {"X-Jarves": "1"}
    r, body = await asyncio.to_thread(req, d, "POST", "/api/jobs", headers=h,
                                      body={"when": "כל יום ב-23:59", "task": "x", "action": "reminder"})
    jid = json.loads(body)["id"]
    await asyncio.to_thread(req, d, "POST", f"/api/jobs/{jid}", headers=h, body={"op": "approve"})
    t0 = time.monotonic()
    await asyncio.to_thread(req, d, "POST", f"/api/jobs/{jid}", headers=h, body={"op": "run_now"})
    while time.monotonic() - t0 < 5 and not core.ops.db.rows("SELECT id FROM job_runs WHERE job_id=?", (jid,)):
        await asyncio.sleep(0.05)
    assert core.ops.db.rows("SELECT id FROM job_runs WHERE job_id=?", (jid,)), "run_now waited for the 30 s poll"
    assert time.monotonic() - t0 < 5


async def test_q05_silent_server_after_start_gets_one_greeting_nudge(monkeypatch):
    """Q05: GPT-Live sometimes sends nothing after session.started (2 of 14 QA starts); the start then failed at 15 s.
    Now one silent clock note is sent after GREETING_NUDGE_S, and audio arriving after it still makes the start
    succeed."""
    import asyncio
    import base64

    from jarves.skills import SkillRegistry
    from jarves.voice import live as live_mod
    from jarves.voice.live import LiveVoice
    from test_voice_faults import FakeClient, loud
    monkeypatch.setattr(live_mod, "GREETING_NUDGE_S", 0.05)
    client = FakeClient()
    voice = LiveVoice(SkillRegistry(), lambda: "backend", client=client, greeting="שלום", hold_output=True,
                      clock_context=False)
    await voice.start()
    sent = []

    class Thinking:
        async def append(self, content, delegation_id=None, event_id=None):
            sent.append(event_id)
            client.conn.push({"type": "session.output_audio.delta", "delta": base64.b64encode(loud(4800)).decode()})
    client.conn.session.thinking = Thinking()
    try:
        await asyncio.wait_for(voice.wait_ready(timeout=2), 3)
        assert sent == ["jarves_greeting_nudge"]
    finally:
        await voice.stop()


def test_q06_no_ai_review_starts_while_the_core_stops(tmp_path, monkeypatch):
    """Q06: a conversation ended BY the shutdown started the paid AI review in a daemon thread that outlived "Core
    stopped cleanly" (budget reservation held, memory.db locked: suite_voice 25 and 28 cycle 4). The Core now turns
    AI reviews off at CORE_STOPPING; the rule-based diagnosis still runs."""
    import json
    import time

    from jarves import diagnose
    from jarves import store as store_mod
    from jarves.config import Config
    rules, ai = [], []
    monkeypatch.setattr(diagnose, "run", lambda st, cfg, sids: rules.extend(sids) or [])
    monkeypatch.setattr(diagnose, "schedule_review", lambda st, cfg, sid: ai.append(sid))
    st = store_mod.Store(tmp_path / "store.db")
    monkeypatch.setattr(st, "ingest", lambda rec: rec.get("sid"))
    sink = store_mod.StoreSink(st, Config())
    try:
        sink._q.put(json.dumps({"sid": "before-stop"}))
        t0 = time.monotonic()
        while ai != ["before-stop"] and time.monotonic() - t0 < 3:
            time.sleep(0.02)
        sink.ai_reviews = False                         # what Core.stop() does at CORE_STOPPING
        sink._q.put(json.dumps({"sid": "ended-by-stop"}))
    finally:
        sink.close()
        st.close()
    assert ai == ["before-stop"] and rules == ["before-stop", "ended-by-stop"]


async def test_q06_core_stop_switches_ai_reviews_off(core):
    assert core.history is None or core.history.ai_reviews is True
    await core.stop()
    assert core.history is None or core.history.ai_reviews is False


def test_q01_cli_features_knows_addon_switches(tmp_path):
    """Q01: a fresh `jarvis features` process (no Core imported) listed only the core catalog: `features off
    scheduler` / `notes` / `backup` failed with "unknown feature" (rc 2) although the dashboard showed them."""
    r = _cli(tmp_path, "features", "off", "scheduler")
    assert r.returncode == 0, r.stderr
    listing = _cli(tmp_path, "features", "list").stdout
    for fid in ("scheduler", "notes", "proactive_alerts", "backup", "appearance"):
        assert fid in listing
    assert "scheduler" in listing and "off" in next(l for l in listing.splitlines() if l.startswith("scheduler"))
