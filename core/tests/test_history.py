"""Conversation history store, cost estimates and self-diagnosis (WP-B, #17 items 8, 9, 16).

Fixture: the user's real 19 Oct 2026 live session (jarves.log JSON lines, home path anonymised): a ~90 s web-search
stall on the Intel stock question and the confusing "סגור" filler the user asked about.
"""
import json
import logging
import time
from pathlib import Path

import pytest

from jarves import diagnose, logs, store
from jarves.config import Config
from jarves.store import Store, daily_costs, session_cost

FIXTURE = Path(__file__).parent / "fixtures" / "session_2026-10-19.jsonl"


@pytest.fixture
def st():
    s = Store(":memory:", tier="priority", backend_model="gpt-6-luna")
    yield s
    s.close()


def _issues(st):
    return {i["fingerprint"]: i for i in st.issues(None)}


def test_real_session_is_reconstructed(st):
    ended = st.ingest_log(FIXTURE)
    assert len(ended) == 1
    s = st.session(ended[0])
    assert (s["model"], s["backend_model"], s["voice_seconds"], s["end_reason"]) == ("gpt-live-1", "gpt-6-luna", 196.0, "user")
    tl = st.timeline(s["id"])
    users = [t for t in tl["turns"] if t["role"] == "user"]
    assert len(users) == 10 and users[0]["text"] == "מה שלומך ג'ארויס"
    assert [t["name"] for t in tl["tools"]] == ["music_play", "music_status", "music_volume", "music_status",
                                                 "music_volume", "music_pause"]
    play = tl["tools"][0]
    assert play["ok"] == 1 and play["code"] == "ok" and play["duration_s"] == pytest.approx(8.46, abs=0.01)
    assert len(tl["delegations"]) == 11
    assert json.loads(s["stats"])["early_response_drops"] == 12


def test_session_cost_estimate(st):
    cfg = Config()
    sid = st.ingest_log(FIXTURE)[0]
    c = session_cost(st, st.session(sid), cfg.pricing)
    assert c["voice"] == pytest.approx(196 / 60 * 0.05, abs=1e-4)          # $0.05 per voice minute, per second
    # 11 backend responses with estimated tokens (3000 in / 150 out) on gpt-6-luna at the priority tier (2x)
    per = (3000 * 0.10 + 150 * 0.50) / 1e6 * 2
    assert c["backend"] == pytest.approx(11 * per, abs=1e-5)
    assert c["estimated_tokens"] is True
    days = daily_costs(st, cfg.pricing, days=3650)
    assert len(days) == 1 and days[0]["sessions"] == 1 and days[0]["total"] == pytest.approx(c["total"], abs=1e-3)


def test_logged_tokens_plan_and_web_search_are_priced(st):
    cfg = Config()
    st.ingest({"ts": "2026-10-20T10:00:00+00:00", "code": "LIVE_STARTED", "session": "a",
               "msg": "GPT-Live session started (gpt-live-1 -> gpt-6.1-sol)"})
    st.ingest({"ts": "2026-10-20T10:00:05+00:00", "code": "LIVE_BACKEND_DONE", "session": "a",
               "msg": "backend response completed", "backend_s": 2.0, "input_tokens": 10000, "cached_tokens": 4000,
               "output_tokens": 1000, "web_searches": 1, "tier": "flex"})
    sid = st.ingest({"ts": "2026-10-20T10:01:00+00:00", "code": "VOICE_ENDED", "session": "a", "reason": "idle",
                     "voice_seconds": 60.0})
    c = session_cost(st, st.session(sid), cfg.pricing)
    expect = (6000 * 2.0 + 4000 * 0.10 + 1000 * 10.0) / 1e6 * 0.5 + 0.01
    assert c["backend"] == pytest.approx(expect, abs=1e-6) and c["voice"] == pytest.approx(0.05)
    assert c["estimated_tokens"] is False
    st.ingest({"ts": "2026-10-20T11:00:00+00:00", "code": "AI_REPLY", "session": "a", "msg": "ChatGPT plan replied",
               "model_calls": 3})
    assert daily_costs(st, cfg.pricing, 3650)[0]["text"] == 0.0          # ChatGPT plan: no API spending


def test_diagnosis_flags_the_real_stall_and_confusing_filler(st):
    cfg = Config()
    ended = st.ingest_log(FIXTURE)
    diagnose.run(st, cfg, ended)
    iss = _issues(st)
    stall = iss["backend_stall"]
    ev = json.loads(stall["evidence"])
    assert stall["severity"] == "high" and ev["seconds"] >= 90
    assert "אינטל" in ev["request"] and ev["impatience"] and ev["outcome"].startswith("never answered")
    assert iss["silent_wait"]["severity"] == "high" and json.loads(iss["silent_wait"]["evidence"])["seconds"] > 30
    filler = iss["filler:סגור"]
    assert filler["severity"] == "high" and "user asked" in filler["title"]
    assert "לא הבנתי" in json.loads(filler["evidence"])["user_reaction"][0]
    assert "user_confusion" in iss and "early_response" in iss and "slow_tool:music_play" in iss
    # the misheard "מה השעה" ("משה עכשיו") made the user ask again
    assert "user_repeated" in iss
    # shutdown noise (face window closed with the conversation) is not an issue
    assert not any(k.startswith("event:LINK_FACE") for k in iss)
    assert not any(k.startswith(("tool_fail", "backend_failed", "cost_")) for k in iss)


def test_issues_are_deduplicated_and_reopened(st):
    cfg = Config()
    ended = st.ingest_log(FIXTURE)
    diagnose.run(st, cfg, ended)
    n = len(st.issues(None))
    diagnose.run(st, cfg, ended)                     # same conversation again: nothing changes
    assert len(st.issues(None)) == n and _issues(st)["backend_stall"]["count"] == 1
    stall = _issues(st)["backend_stall"]
    st.set_issue_status(stall["id"], "resolved")
    # the same log replayed as another Core run = another conversation with the same problem
    st2 = [dict(json.loads(l), session="second") for l in FIXTURE.read_text(encoding="utf-8").splitlines()]
    for d in st2:
        d["ts"] = d["ts"].replace("2026-10-19", "2026-10-20")
    second = [sid for sid in map(st.ingest, st2) if sid]
    assert len(second) == 1 and second != ended
    diagnose.run(st, cfg, second)
    stall = _issues(st)["backend_stall"]
    assert stall["count"] == 2 and stall["status"] == "open" and len(st.issues(None)) == n


def test_privacy_mode_keeps_only_lengths_and_raises_no_text_issues(st):
    fmt = logs.JsonFormatter()
    logs.set_privacy(True)
    try:
        for line in FIXTURE.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            rec = logging.LogRecord("jarves", logging.INFO, "", 0, d["msg"], None, None)
            rec.created = store.parse_ts(d["ts"])
            rec.component, rec.code = d["component"], d["code"]
            rec.fields = {k: v for k, v in d.items() if k not in ("ts", "level", "component", "code", "msg", "cid",
                                                                 "session")}
            st.ingest(json.loads(fmt.format(rec)))
    finally:
        logs.set_privacy(False)
    turns = st.rows("SELECT * FROM turns")
    assert turns and all(t["text"] is None and t["chars"] > 0 for t in turns)
    assert "אינטל" not in json.dumps(st.rows("SELECT * FROM events"), ensure_ascii=False)
    diagnose.run(st, Config())
    iss = _issues(st)
    assert "early_response" in iss                   # timing rules still work
    assert not any(k.startswith(("filler", "user_", "backend_stall")) for k in iss)


def test_dead_air_from_measured_reply_wait(st):
    for i, (code, extra) in enumerate([("LIVE_STARTED", {"msg": "GPT-Live session started (gpt-live-1 -> gpt-6-luna)"}),
                                       ("LIVE_USER_SAID", {"text": "מה מזג האוויר"}),
                                       ("LIVE_JARVES_SAID", {"text": "עשרים מעלות ושמש.", "wait_s": 7.5}),
                                       ("VOICE_ENDED", {"voice_seconds": 30.0, "reason": "idle"})]):
        sid = st.ingest({"ts": 1_800_000_000 + i * 5, "code": code, "session": "d", **extra})
    diagnose.run(st, Config(), [sid])
    dead = _issues(st)["dead_air"]
    assert dead["severity"] == "medium" and "7.5" in dead["title"]


def test_log_sink_records_live_and_diagnoses_after_the_session(home):
    cfg = Config()
    logs.setup(home / "logs", console=False, privacy=False)
    sink = store.attach(home, cfg)
    try:
        logs.setup(home / "logs", console=False, privacy=False)   # a later setup keeps the sink
        assert sink in logging.getLogger("jarves").handlers
        logs.info("live", "LIVE_STARTED", "GPT-Live session started (gpt-live-1 -> gpt-6-luna)")
        logs.info("live", "LIVE_USER_SAID", "you said", text="תבדוק משהו ברשת")
        logs.info("live", "LIVE_PROGRESS", "slow lookup: short progress note requested", n=1)
        logs.info("live", "LIVE_JARVES_SAID", "JARVES said", text="רגע, אני בודק.")
        logs.info("live", "LIVE_TOOL_CALL", "backend called music_play", delegation="x")
        logs.info("skills", "TOOL_RESULT", "music_play -> PEAR_DOWN", tool="music_play", ok=False, origin="model")
        logs.info("voice", "VOICE_ENDED", "voice session ended (idle)", reason="idle", voice_seconds=40.0)
        deadline = time.time() + 5
        while time.time() < deadline and not sink.store.issues():
            time.sleep(0.05)
        live = sink.store.sessions()
        assert len(live) == 1 and live[0]["voice_seconds"] == 40.0
        assert sink.store.rows("SELECT text FROM turns")[0]["text"] == "תבדוק משהו ברשת"
        fps = {i["fingerprint"] for i in sink.store.issues()}
        assert "tool_fail:music_play" in fps
    finally:
        store.detach(sink)
    assert sink not in logging.getLogger("jarves").handlers
    assert (home / "store.db").is_file()


def test_history_can_be_disabled(home):
    cfg = Config()
    cfg.history.enabled = False
    assert store.attach(home, cfg) is None and not (home / "store.db").exists()


def test_reply_wait_hook_measures_silence_before_reply():
    from jarves.voice.live import LiveVoice
    lv = LiveVoice.__new__(LiveVoice)
    lv._last_user_voice = 100.0
    lv._note_reply_wait(101.4)
    assert lv._take_reply_wait() == {"wait_s": 1.4}
    assert lv._take_reply_wait() == {}               # consumed: the next transcript without a new reply gets none
    lv._note_reply_wait(110.0)                       # JARVES speaks again without the user talking
    assert lv._take_reply_wait() == {}


def test_cli_cost_and_diagnose_import(home, capsys):
    from jarves import cli
    (home / "logs").mkdir()
    (home / "logs" / "jarves.log").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    assert cli.main(["diagnose", "--import-log", "--days", "3650"]) == 0
    out = capsys.readouterr().out
    assert "imported 1 conversation" in out and "Request stalled" in out
    assert cli.main(["diagnose", "--import-log", "--days", "3650"]) == 0      # already recorded: no duplicate
    assert "imported 0 conversation" in capsys.readouterr().out
    assert cli.main(["cost", "--days", "3650"]) == 0
    out = capsys.readouterr().out
    assert "ESTIMATE" in out and "2026-10-19" in out and "0.172" in out
