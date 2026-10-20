"""Self-check (owner request 20 Oct 2026, #17): real Store / UserDB / Scheduler / Autonomy / Features / Core pieces in
tmp dirs, synthetic jarves.log files, a fixed clock for the analysis. Nothing of the analysis is mocked; only the
OpenAI client of the AI review is a local fake (no network), and disk/memory numbers are injected."""
from __future__ import annotations

import asyncio
import io
import json
import re
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jarves import autonomy, features as feat, logs, selfcheck
from jarves.config import Config
from jarves.store import Store
from jarves.userdata import open_db

HE = re.compile(r"[֐-׿]")
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc).timestamp()
SECRET = "תזמין לי שולחן אצל דני ברחוב הרצל"          # conversation text: must never reach a report


def rec(t: float, code: str, level: str = "INFO", comp: str = "core", session: str = "p1", msg: str = "-", **f):
    d = {"ts": datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds"), "level": level,
         "component": comp, "code": code, "msg": msg, "cid": "c1", "session": session, **f}
    return json.dumps(d, ensure_ascii=False)


def write_log(home: Path, lines: list[str]) -> None:
    (home / "logs").mkdir(parents=True, exist_ok=True)
    (home / "logs" / "jarves.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def busy_week(t0: float = T0) -> list[str]:
    """Three Core runs over the last days: p1 crashed (no CORE_STOPPED), p2 and p3 clean."""
    L = []
    for i, p in enumerate(("p1", "p2", "p3")):
        base = t0 - (3 - i) * 86400
        L.append(rec(base, "CORE_STARTING", session=p, msg="JARVES Core starting"))
        L.append(rec(base + 5, "LINK_BROKEN", "ERROR", "link", p, "face link failed: ConnectionResetError"))
        L.append(rec(base + 6, "LINK_BROKEN", "ERROR", "link", p, "face link failed: ConnectionResetError"))
        if p != "p1":
            L.append(rec(base + 3000, "CORE_STOPPED", session=p, msg="Core stopped cleanly"))
    b = t0 - 3600
    L.append(rec(b, "LIVE_USER_SAID", comp="live", session="p3", msg="user said", text=SECRET))
    for k, w in enumerate([2.0, 3.0, 9.5, 11.0, 12.0, 8.0, 14.0]):
        L.append(rec(b + 10 * k, "LIVE_JARVES_SAID", comp="live", session="p3", msg="JARVES said", text=SECRET,
                     wait_s=w))
    for k in range(4):
        L.append(rec(b + 100 + k, "WAKE_WORD", comp="wake", session="p3", msg="wake word heard", score=0.4))
    for k in range(3):
        L.append(rec(b + 200 + k, "VOICE_NO_SPEECH", comp="voice", session="p3", msg="no speech after wake"))
    for k in range(3):
        L.append(rec(b + 300 + k, "TOOL_RESULT", comp="skills", session="p3", msg="gmail_search -> GOOGLE_API_ERROR",
                     tool="gmail_search", ok=False, origin="model"))
    L.append(rec(b + 310, "TOOL_RESULT", comp="skills", session="p3", msg="get_time -> ok", tool="get_time", ok=True))
    for k in range(2):
        L.append(rec(b + 400 + k, "LIVE_FALSE_ACTION_CLAIM", "WARNING", "live", "p3",
                     "JARVES claimed an action that no tool performed in this turn", verb="שלחתי"))
    L.append(rec(b + 500, "AI_BUDGET", "WARNING", "live", "p3", "daily AI request limit reached (live)"))
    L.append(rec(b + 600, "MUSIC_PLAYER_OFFLINE", "WARNING", "music", "p3", "Pear Desktop not reachable"))
    L.append(rec(t0 - 40 * 86400, "OLD_ERROR", "ERROR", "core", "p0", "outside the window"))
    return L


def sysinfo(free=0.8, avail=4000):
    return lambda home: {"disk_free_gb": free, "memory": {"total_mb": 8000, "available_mb": avail}, "state_mb": 12.0}


def check(home, cfg=None, **kw) -> selfcheck.SelfCheck:
    kw.setdefault("clock", lambda: T0)
    kw.setdefault("system", sysinfo())
    return selfcheck.SelfCheck(home, cfg or Config(), **kw)


def fps(v) -> list[str]:
    return [f["fingerprint"] for f in v["findings"]]


# ====================================================================== the analysis
def test_full_run_ranks_log_health_system_findings_with_evidence_and_hebrew(home):
    write_log(home, busy_week())
    comps = lambda: {"ai": {"status": "ok"}, "audio": {"status": "unavailable", "detail": "no output device",
                                                        "action": "jarvis status", "since": T0 - 60}}
    c = check(home, components=comps)
    try:
        v = c.run("test", ai=False)
    finally:
        c.close()
    got = fps(v)
    for fp in ("health:audio", "crash", "log:ERROR:LINK_BROKEN", "latency", "wake:false", "tool:gmail_search",
               "claims", "budget:limit", "avail:pear", "disk"):
        assert fp in got, (fp, got)
    assert "log:ERROR:OLD_ERROR" not in got                         # outside the 7-day window
    assert "log:WARNING:AI_BUDGET" not in got                       # has its own rule, not counted twice
    sev = [selfcheck.SEV[f["severity"]] for f in v["findings"]]
    assert sev == sorted(sev, reverse=True) and v["findings"][0]["severity"] == "high"
    assert [f["rank"] for f in v["findings"]] == list(range(1, len(got) + 1))
    by = {f["fingerprint"]: f for f in v["findings"]}
    assert by["log:ERROR:LINK_BROKEN"]["severity"] == "high"         # 3 separate runs: a restart does not fix it
    assert "across 3 runs" in by["log:ERROR:LINK_BROKEN"]["title"]
    assert any("LINK_BROKEN" in e for e in by["log:ERROR:LINK_BROKEN"]["evidence"])
    assert by["crash"]["count"] == 1 and "p1" in by["crash"]["evidence"][0]
    assert by["tool:gmail_search"]["count"] == 3 and "GOOGLE_API_ERROR" in by["tool:gmail_search"]["title"]
    assert by["wake:false"]["action"] == {"type": "setting", "key": "voice.wake_threshold", "value": 0.4}
    assert by["disk"]["severity"] == "high"
    lat = v["metrics"]["latency"]
    assert lat["replies"] == 7 and lat["p50_s"] == 9.5 and lat["p90_s"] == pytest.approx(12.8) and lat["slow"] == 5
    assert v["metrics"]["crashes"] == 1 and v["metrics"]["starts"] == 3
    assert v["metrics"]["wake"] == {"wakes": 4, "false_wakes": 3, "near_misses": 0, "threshold": 0.35}
    for f in v["findings"]:                                          # every fixed text has Hebrew
        assert HE.search(f["title_he"]) and HE.search(f["fix_he"]), f["fingerprint"]
        assert f["cause"] and f["fix"] and f["effort"] in selfcheck.EFFORT_HE
    assert v["counts"]["high"] >= 3 and 0 <= v["score"] < 100
    assert re.match(r"\d+ issues \(\d+ serious\), the main one is: ", v["summary"])
    assert v["summary_he"].startswith("מצאתי") and "העיקרית" in v["summary_he"]
    st = Store(home / "store.db")
    try:
        assert st.rows("SELECT COUNT(*) n FROM selfcheck_findings")[0]["n"] == len(got)
        assert st.rows("SELECT status, trigger FROM selfcheck_runs")[0] == {"status": "done", "trigger": "test"}
    finally:
        st.close()


def test_no_conversation_text_reaches_the_report_or_the_ticket(home):
    write_log(home, busy_week())
    c = check(home)
    v = c.run("test", ai=False)
    c.close()
    blob = json.dumps(v, ensure_ascii=False) + json.dumps(selfcheck.ticket_draft(v), ensure_ascii=False)
    assert SECRET not in blob and "דני" not in blob
    t = selfcheck.ticket_draft(v)
    assert t["title"].startswith("[self-check] ") and "| # | Severity |" in t["body"] and "(#17)" in t["body"]


def test_config_sanity_and_feature_gaps(home):
    cfg = Config()
    cfg.voice.progress_after_s = 0
    cfg.voice.backend_deadline_s = 30
    cfg.log.level = "DEBUG"
    cfg.link.host = "0.0.0.0"
    write_log(home, [rec(T0 - 60, "CORE_STARTING")])
    f = feat.Features(cfg, home)
    f.set("music", True)
    c = check(home, cfg, features=f)
    v = c.run("test", ai=False)
    c.close()
    by = {x["fingerprint"]: x for x in v["findings"]}
    assert by["config:link_host"]["severity"] == "high"
    assert by["config:no_progress"]["action"] == {"type": "setting", "key": "voice.progress_after_s", "value": 2.5}
    assert by["config:deadline_late"]["action"]["value"] == 15.0
    assert by["config:log_level"]["action"]["value"] == "INFO"
    assert "setup" in by                                            # fresh home: setup never finished
    assert all(not k.startswith("bundle:") for k in by), [k for k in by if k.startswith("bundle:")]


def test_unused_and_useful_off_features_are_suggested(home):
    from jarves import setup_wizard
    setup_wizard.load_addons()
    write_log(home, [rec(T0 - 60, "CORE_STARTING")])
    st = Store(home / "store.db")
    for i in range(3):
        st.x("INSERT INTO sessions (id, kind, started, ended) VALUES (?, 'live', ?, ?)", (f"s{i}", T0 - 900 - i, T0 - 800))
    st.commit()
    f = feat.Features(Config(), home)
    f.set("backup", False)
    c = check(home, store=st, features=f)
    v = c.run("test", ai=False)
    c.close()
    st.close()
    by = {x["fingerprint"]: x for x in v["findings"]}
    assert by["useful:backup"]["action"] == {"type": "feature_on", "feature": "backup"}
    assert "unused:notes" in by and HE.search(by["unused:notes"]["fix_he"])
    assert not any(k.startswith("useful:") and k.split(":")[1] in setup_wizard.SAFE_OFF for k in by)


def test_update_available_from_the_cached_check(home):
    (home / "updates.json").write_text(json.dumps({"release": {"version": "999.0.0"}, "checked_at": "x"}),
                                       encoding="utf-8")
    write_log(home, [])
    c = check(home)
    v = c.run("test", ai=False)
    c.close()
    u = next(x for x in v["findings"] if x["fingerprint"] == "update")
    assert "999.0.0" in u["title"] and u["action"]["command"] == "jarvis update install"


# ====================================================================== history, trend, suggestions, approvals
def test_trend_against_the_previous_run(home):
    write_log(home, busy_week())
    c = check(home)
    first = c.run("test", ai=False)
    write_log(home, [rec(T0 - 100, "CORE_STARTING", session="p9")] +
              [rec(T0 - 50 + k, "NEW_FAILURE", "ERROR", "core", "p9", "boom") for k in range(3)])
    second = c.run("test", ai=False)
    c.close()
    tr = second["trend"]
    assert tr["previous_run"] == first["id"] and "log:ERROR:NEW_FAILURE" in tr["new"]
    assert "crash" in tr["resolved"] and "log:ERROR:LINK_BROKEN" in tr["resolved"]
    assert tr["score_delta"] == second["score"] - first["score"] > 0
    assert next(f for f in second["findings"] if f["fingerprint"] == "log:ERROR:NEW_FAILURE")["trend"] == "new"
    st = Store(home / "store.db")
    assert [r["id"] for r in selfcheck.runs(st)] == [second["id"], first["id"]]
    st.close()


def test_accept_creates_a_proposal_and_never_applies_until_approved(home):
    write_log(home, busy_week())
    (home / "config.toml").write_text("[voice]\nwake_threshold = 0.35\n", encoding="utf-8")
    cfg = Config()
    c = check(home, cfg)
    c.run("test", ai=False)
    st, db = c.store, c.db
    r = selfcheck.decide(st, "wake:false", "accept", by="dashboard", home=home, cfg=cfg, db=db, clock=lambda: T0)
    assert r["state"] == "accepted" and r["needs_approval"]
    p = db.row("SELECT * FROM proposals WHERE id=?", (r["proposal_id"],))
    assert p["status"] == "proposed" and p["applier"] == "wake_threshold"
    assert "0.35" in (home / "config.toml").read_text(encoding="utf-8")            # not applied
    again = selfcheck.decide(st, "wake:false", "accept", by="dashboard", home=home, cfg=cfg, db=db, clock=lambda: T0)
    assert again["proposal_id"] == r["proposal_id"]                                 # reused, not duplicated
    a = autonomy.Autonomy(db, cfg=cfg, home=home)
    a.sync([{"fingerprint": "false_wake", "kind": "wake", "count": 3, "status": "open", "severity": "low",
             "title": "False wake", "evidence": ""}])
    assert db.row("SELECT COUNT(*) n FROM proposals")["n"] == 1                       # same key as autonomy.sync
    done = a.decide(r["proposal_id"], True, by="dashboard")
    assert done["status"] == "applied" and "wake_threshold = 0.4" in (home / "config.toml").read_text(encoding="utf-8")
    c.close()


def test_config_setting_applier_validates_applies_and_reverts(home):
    cfg = Config()
    cfg.voice.progress_after_s = 0
    (home / "config.toml").write_text("[voice]\nprogress_after_s = 0\n", encoding="utf-8")
    write_log(home, [])
    c = check(home, cfg)
    c.run("test", ai=False)
    r = selfcheck.decide(c.store, "config:no_progress", "accept", by="cli", home=home, cfg=cfg, db=c.db,
                         clock=lambda: T0)
    a = autonomy.Autonomy(c.db, cfg=cfg, home=home)
    p = a.decide(r["proposal_id"], True, by="cli")
    assert p["status"] == "applied" and "progress_after_s = 2.5" in (home / "config.toml").read_text(encoding="utf-8")
    a.revert(p["id"], by="cli")
    assert "progress_after_s = 0" in (home / "config.toml").read_text(encoding="utf-8")
    assert autonomy.APPLIERS["config_setting"].risk == "medium"
    with pytest.raises(ValueError):
        a.set_grants(["config_setting"], by="dashboard")                           # never automatic
    with pytest.raises(ValueError):
        selfcheck.propose({"fingerprint": "x", "action": {"type": "setting", "key": "link.host", "value": "x"},
                           "fix_he": "x", "title": "x", "fix": "x"}, home=home, cfg=cfg, db=c.db)
    c.close()


def test_feature_on_applier_refuses_privacy_sensitive_features(home):
    db = open_db(home)
    a = autonomy.Autonomy(db, cfg=Config(), home=home, features=feat.Features(Config(), home))
    cur = db.x("INSERT INTO proposals (fingerprint, title, applier, params, risk, status, created) VALUES "
               "('t','t','feature_on',?,'medium','proposed',0)", (json.dumps({"feature": "camera"}),))
    p = a.decide(cur.lastrowid, True, by="dashboard")
    assert p["status"] == "failed" and "privacy-sensitive" in p["result"]
    db.close()


def test_dismiss_and_snooze_hide_a_suggestion_until_the_snooze_ends(home):
    write_log(home, busy_week())
    now = [T0]
    c = check(home, clock=lambda: now[0])
    v = c.run("test", ai=False)
    top = v["findings"][0]["fingerprint"]
    selfcheck.decide(c.store, top, "dismiss", by="dashboard", home=home, cfg=c.cfg, db=c.db, clock=lambda: now[0])
    snz = v["findings"][1]["fingerprint"]
    r = selfcheck.decide(c.store, snz, "snooze", by="dashboard", home=home, cfg=c.cfg, db=c.db, days=2,
                         clock=lambda: now[0])
    assert r["until"] == T0 + 2 * 86400
    v2 = selfcheck.run_view(c.store, now=now[0])
    state = {f["fingerprint"]: f["state"] for f in v2["findings"]}
    assert state[top] == "dismissed" and state[snz] == "snoozed" and v2["open"] == v["open"] - 2
    now[0] = T0 + 3 * 86400
    assert {f["fingerprint"]: f["state"] for f in selfcheck.run_view(c.store, now=now[0])["findings"]}[snz] == "open"
    with pytest.raises(KeyError):
        selfcheck.decide(c.store, "nope", "dismiss", by="x", home=home, cfg=c.cfg, db=c.db)
    c.close()


# ====================================================================== the AI pass (existing review path)
class FakeOpenAI:
    """Local stand-in for the OpenAI client (no network); counts requests."""

    def __init__(self):
        self.calls = []
        outer = self

        class R:
            def create(self, **kw):
                outer.calls.append(kw)

                class U:
                    input_tokens, output_tokens = 1000, 100
                    input_tokens_details = None

                class Resp:
                    model, usage = "gpt-6-luna", U()
                    output_text = json.dumps({"issues": [{"title": "Ignored the question", "severity": "medium",
                                                          "detail": "d", "quote": "q"}]})
                return Resp()
        self.responses = R()


def _voice_session(st, sid, t):
    st.x("INSERT INTO sessions (id, kind, started, ended) VALUES (?, 'live', ?, ?)", (sid, t, t + 60))
    for k, role in enumerate(("user", "assistant", "user", "assistant")):
        st.x("INSERT INTO turns (session_id, ts, role, text, chars) VALUES (?,?,?,?,4)", (sid, t + k, role, "שלום"))
    st.commit()


def test_ai_pass_uses_the_existing_review_within_caps_and_shows_cost(home, monkeypatch):
    import time as _time
    write_log(home, [])
    now = _time.time()
    st = Store(home / "store.db")
    for i in range(3):
        _voice_session(st, f"v{i}", now - 3600 - i)
    client = FakeOpenAI()
    c = check(home, store=st, clock=lambda: now, ai_client=client)
    v = c.run("test", ai=True)
    assert len(client.calls) == 2                                   # self_check.ai_sessions = 2
    assert v["ai"]["used"] and v["ai"]["reviewed"] == 2 and v["ai"]["cost_usd"] > 0
    assert "issue:ai:" in " ".join(fps(v)) or "issues:other" in fps(v)
    assert "$" in v["summary"] and "$" in v["summary_he"]
    off = c.run("test", ai=False)
    assert not off["ai"]["used"] and len(client.calls) == 2
    c.close()
    st.close()


def test_ai_pass_skips_without_a_key_route_and_in_privacy_mode(home):
    import time as _time
    now = _time.time()
    write_log(home, [])
    st = Store(home / "store.db")
    _voice_session(st, "v1", now - 100)
    c = check(home, store=st, clock=lambda: now)
    v = c.run("test", ai=True)
    assert not v["ai"]["used"] and "API key" in v["ai"]["skipped"]
    cfg = Config()
    cfg.log.privacy = True
    c2 = check(home, cfg, store=st, clock=lambda: now, ai_client=FakeOpenAI())
    v2 = c2.run("test", ai=True)
    assert not v2["ai"]["used"] and "privacy" in v2["ai"]["skipped"]
    c.close()
    c2.close()
    st.close()


# ====================================================================== feature registration / scheduler / CLI
def test_registered_grouped_bundled_guided_and_declared():
    from jarves import capabilities as cap
    from jarves import guides
    from jarves import setup_wizard as sw
    sw.load_addons()
    assert selfcheck.FEATURE in feat.CATALOG and feat.CATALOG["self_check"].default_enabled
    assert "self_check" in {i for _, _, _, ids in sw.GROUPS for i in ids}
    assert all(ok for ok, _ in (chk() for chk in sw.BUNDLE["self_check"]))
    assert guides.for_feature("self_check").id == "self_check"
    assert cap.view("self_check")["problems"] == [] and cap.view("self_check")["declared"]
    assert HE.search(feat.CATALOG["self_check"].title_he)


def test_weekly_job_is_created_once_and_its_time_can_change(home):
    from jarves.scheduler import Scheduler
    db = open_db(home)
    s = Scheduler(db, clock=lambda: T0)
    jid = selfcheck.ensure_job(s, db)
    j = s.view(s.job(jid))
    assert j["status"] == "active" and j["spec"] == "0 10 * * 0" and j["tool"] == "self_check_run"
    assert selfcheck.ensure_job(s, db) == jid and len(s.jobs()) == 1
    v = selfcheck.set_schedule(s, db, "every monday at 23", by="cli")
    assert v["spec"] == "0 23 * * 1" and v["status"] == "active"
    assert "quiet hours" in selfcheck.quiet_note(db, v, s)
    assert selfcheck.set_schedule(s, db, "off", by="cli") is None and s.job(jid)["status"] == "paused"
    s.delete(jid, by="user")
    assert selfcheck.ensure_job(s, db) is None                       # a deleted job is not recreated by itself
    db.close()


def test_cli_now_history_schedule_and_suggestion(home):
    from jarves.cli import main
    write_log(home, busy_week(__import__("time").time()))
    out = io.StringIO()
    with redirect_stdout(out):
        assert main(["diagnose", "--now", "--no-ai", "--ticket"]) == 0
    text = out.getvalue()
    assert "SELF-CHECK #1" in text and "ticket draft (not posted)" in text and "evidence:" in text
    out = io.StringIO()
    with redirect_stdout(out):
        assert main(["diagnose", "--history"]) == 0
        assert main(["diagnose", "--schedule", "every sunday at 9"]) == 0
        assert main(["diagnose", "--suggestion", "crash", "--op", "dismiss"]) == 0
        assert main(["diagnose", "--suggestion", "missing"]) == 2
    text = out.getvalue()
    assert "#1" in text and "self-check scheduled: " in text and "crash: dismissed" in text


def test_cli_plain_diagnose_keeps_its_30_day_default(home):
    from jarves.cli import main
    out = io.StringIO()
    with redirect_stdout(out):
        assert main(["diagnose"]) == 0
    assert "self-diagnosis:" in out.getvalue()


# ====================================================================== dashboard API
def test_dashboard_run_view_decide_ticket_and_settings(home):
    from jarves import dashboard
    from test_dashboard import req
    write_log(home, busy_week(__import__("time").time()))
    H = {"X-Jarves": "1"}
    d = dashboard.Dashboard(home, Config()).start()
    try:
        r, body = req(d, "POST", "/api/selfcheck/run", headers=H, body={"ai": False})
        assert r.status == 200
        v = json.loads(body)
        assert v["findings"] and v["trigger"] == "dashboard"
        r, body = req(d, "GET", "/api/selfcheck")
        page = json.loads(body)
        assert page["latest"]["id"] == v["id"] and page["history"][0]["id"] == v["id"]
        assert page["settings"]["self_check.days"] == 7 and page["enabled"] is True
        fp = next(f["fingerprint"] for f in v["findings"] if f["changes_setting"])
        r, body = req(d, "POST", "/api/selfcheck/decide", headers=H, body={"fingerprint": fp, "op": "accept"})
        assert r.status == 200 and json.loads(body)["proposal_id"]
        r, body = req(d, "GET", f"/api/selfcheck/{v['id']}/ticket")
        assert r.status == 200 and json.loads(body)["title"].startswith("[self-check]")
        r, body = req(d, "POST", "/api/selfcheck/settings", headers=H, body={"self_check.on_start": "always",
                                                                 "self_check.error_burst": 3})
        assert r.status == 200 and json.loads(body)["self_check.on_start"] == "always"
        r, _ = req(d, "POST", "/api/selfcheck/settings", headers=H, body={"self_check.on_start": "sometimes"})
        assert r.status == 400
        r, _ = req(d, "POST", "/api/selfcheck/decide", headers=H, body={"fingerprint": "nope", "op": "dismiss"})
        assert r.status == 404
        r, body = req(d, "GET", "/static/selfcheck.js")
        assert r.status == 200 and b"PAGES, { selfcheck" in body and b".innerHTML" not in body
    finally:
        d.stop()


# ====================================================================== inside a real Core
async def _settle(svc):
    for _ in range(200):                  # let call_soon_threadsafe callbacks / spawned tasks start (no timers)
        await asyncio.sleep(0)
        if svc._tasks:
            break
    await asyncio.gather(*list(svc._tasks), return_exceptions=True)


async def test_core_runs_a_check_after_an_unclean_stop_and_on_voice_request(home, cfg):
    import time as _time
    from conftest import FakeTTS, FakeSTT
    from jarves.app import Core
    from jarves.skills.music import FakePlayer
    from jarves.voice.audio import SimAudio
    t = _time.time()
    write_log(home, [rec(t - 7200, "CORE_STARTING", session="old1"), rec(t - 7100, "SOMETHING", session="old1")])
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    try:
        svc = c.ops.selfcheck
        await _settle(svc)
        assert svc.last is not None and svc.last["trigger"] == "crash"
        alerts = c.ops.db.rows("SELECT * FROM alerts WHERE source='selfcheck'")
        assert alerts and alerts[0]["priority"] in ("normal", "high") and HE.search(alerts[0]["title"])
        jid = c.ops.db.setting("self_check.job_id")
        assert c.ops.scheduler.job(jid)["status"] == "active"
        r = await c.registry.call("self_check_run", {"days": 2}, origin="model")
        assert r.ok and HE.search(r.message) and r.data["run"] == svc.last["id"] and svc.last["trigger"] == "voice"
        # the scheduled job runs the same tool and saves a report
        c.ops.scheduler.run_now(jid, by="test")
        await c.ops.scheduler.tick()
        await asyncio.gather(*[x for s in c.ops.scheduler._running.values() for x in s], return_exceptions=True)
        rep = c.ops.db.rows("SELECT * FROM reports WHERE job_id=?", (jid,))
        assert rep and rep[0]["status"] == "ok" and HE.search(rep[0]["text"])
        assert svc.last["trigger"] == "schedule"
    finally:
        await c.stop()


async def test_core_error_burst_starts_one_check(home, cfg):
    from conftest import FakeTTS, FakeSTT
    from jarves.app import Core
    from jarves.skills.music import FakePlayer
    from jarves.voice.audio import SimAudio
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    try:
        svc = c.ops.selfcheck
        await _settle(svc)
        assert svc.last is None                                     # fresh home: no previous run, nothing started
        c.ops.db.set_setting("self_check.error_burst", 3)
        c.ops.db.set_setting("self_check.min_interval_s", 0)
        for _ in range(3):
            logs.error("test", "TEST_ERROR", "synthetic error")
        await _settle(svc)
        assert svc.last is not None and svc.last["trigger"] == "errors"
        assert any(f["fingerprint"] == "log:ERROR:TEST_ERROR" for f in svc.last["findings"])
    finally:
        await c.stop()
