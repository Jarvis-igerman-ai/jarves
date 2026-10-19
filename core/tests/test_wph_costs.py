"""SW-V2 WP-H: exact backend cost from the BackendTrace log events (model, tier, usage, web-search calls)."""
from jarves.config import Config
from jarves.store import Store, daily_costs, session_cost
from jarves.voice import live as live_mod


def _rec(code, ts, **f):
    return {"ts": ts, "level": "INFO", "component": "live", "code": code, "session": "s1", **f}


def test_backend_start_done_gives_exact_cost_and_counts_web_searches():
    cfg = Config()
    st = Store(":memory:", tier="priority", backend_model="gpt-6-luna")
    t = 1_760_000_000.0
    st.ingest(_rec("LIVE_STARTED", t, msg="live conversation started (gpt-live-1 -> gpt-6-luna)"))
    st.ingest(_rec("LIVE_BACKEND_START", t + 1, msg="backend request started", response_id="r1", model="gpt-6-luna"))
    sid = st._cur
    open_rows = st.rows("SELECT * FROM delegations WHERE session_id = ?", (sid,))
    assert open_rows[0]["status"] == "started"              # a stalled request is visible before it finishes
    st.ingest(_rec("LIVE_BACKEND_DONE", t + 5, msg="backend response completed", response_id="r1", backend_s=4.0,
                   model="gpt-6-luna-2026-09", tier="priority", usage_in=2000, usage_out=100, usage_cached=1000,
                   web_searches=2, searches=1))
    st.ingest(_rec("TASK_BACKEND_DONE", t + 20, msg="backend response completed", response_id="r2", usage_in=1000,
                   usage_out=1000, web_searches=3, model="gpt-6-luna"))
    st.ingest(_rec("VOICE_ENDED", t + 60, msg="ended", voice_seconds=60.0, reason="user"))
    rows = st.rows("SELECT * FROM delegations WHERE session_id = ? ORDER BY id", (sid,))
    assert len(rows) == 2 and rows[0]["status"] == "completed" and rows[0]["model"] == "gpt-6-luna-2026-09"
    c = session_cost(st, st.session(sid), cfg.pricing)
    # voice: 1 min x $0.05. backend r1 (unknown dated model -> luna price, priority 2x):
    # (1000*0.10 + 1000*0.01 + 100*0.50)/1e6*2 = 0.00032 + 2 searches 0.02; task r2: (1000*0.1+1000*0.5)/1e6 + 0.03
    assert c["voice"] == 0.05 and not c["estimated_tokens"] and c["web_searches"] == 5
    assert abs(c["backend"] - (0.00032 + 0.02 + 0.0006 + 0.03)) < 1e-6
    assert daily_costs(st, cfg.pricing, 3650)[0]["web_searches"] == 5


def test_ai_reply_reads_usage_fields():
    cfg = Config()
    st = Store(":memory:", backend_model="gpt-6-luna")
    st.ingest({"ts": 1_760_000_000.0, "level": "INFO", "component": "ai", "code": "AI_REPLY", "msg": "agent replied",
               "model_calls": 2, "usage_in": 4000, "usage_out": 200, "web_searches": 1, "model": "gpt-6-luna"})
    r = st.rows("SELECT * FROM delegations")[0]
    assert (r["input_tokens"], r["output_tokens"], r["web_searches"]) == (4000, 200, 1)
    d = daily_costs(st, cfg.pricing, 3650)[0]
    assert abs(d["text"] - ((4000 * 0.10 + 200 * 0.50) / 1e6 + 0.01)) < 1e-9


def test_progress_notes_are_neutral_and_front_knows_background_tasks():
    for note in (live_mod.PROGRESS_NOTE, live_mod.PROGRESS_NOTE_AGAIN):
        assert "כמעט שם" not in note and "almost ready (" not in note
    text = live_mod.front_instructions()
    assert "[task] note" in text and "more than twice" in text
