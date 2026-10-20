"""WP-M3 notes, proactive alerts and bounded autonomy (offline: fake outputs, fake clock)."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from jarves import alerts as A
from jarves import autonomy as AU
from jarves import notes as N
from jarves.config import Config
from jarves.skills import SkillRegistry
from jarves.userdata import UserDB

JLM = ZoneInfo("Asia/Jerusalem")


def at(h, m=0, d=19) -> float:
    return datetime(2026, 10, d, h, m, tzinfo=JLM).timestamp()


@pytest.fixture
def db(tmp_path):
    d = UserDB(tmp_path / "memory.db")
    yield d
    d.close()


# ---------------------------------------------------------------------- notes
async def test_notes_crud_search_export_and_delete_needs_approval(db, tmp_path):
    notes = N.Notes(db)
    a = notes.add("קניות", "חלב, ביצים", ["בית", "#Shopping"])
    notes.add("רעיון", "רובוט שמדבר עברית", "ideas work")
    assert a["tags"] == ["בית", "shopping"] and a["created"] == a["updated"]
    assert [n["title"] for n in notes.search("ביצים")] == ["קניות"]
    assert [n["title"] for n in notes.search(tag="ideas")] == ["רעיון"]
    assert [n["title"] for n in notes.search("shopping")] == ["קניות"]
    e = notes.edit(a["id"], append="לחם")
    assert e["text"].endswith("לחם") and e["updated"] >= e["created"]
    md = notes.markdown([e])
    assert md.startswith("# קניות") and "#shopping" in md and "לחם" in md
    reg = SkillRegistry(allowed_actions=N.ALLOWED_ACTIONS)
    reg.register(N.make_skill(notes))
    assert (await reg.call("note_add", {"title": "x", "text": "y"})).ok
    found = await reg.call("note_find", {"query": "רובוט"})
    assert found.ok and found.data["notes"][0]["title"] == "רעיון"
    r = await reg.call("note_delete", {"note_id": a["id"]})            # model: always asks; no screen -> no
    assert r.code == "TOOL_DECLINED" and notes.get(a["id"]) is not None
    asked = []

    async def yes(prompt):
        asked.append(prompt)
        return True
    reg.confirm = yes
    assert (await reg.call("note_delete", {"note_id": a["id"]})).ok and notes.get(a["id"]) is None
    assert "קניות" in asked[0]
    assert any(x["action"] == "note_delete" for x in db.audit_log())


def test_notes_dashboard_routes(db, tmp_path):
    ctx = SimpleNamespace(home=tmp_path, cfg=None, _userdata=db)
    st, n = N.api_note_add(ctx, None, {"title": "t", "text": "body", "tags": "a,b"})
    assert st == 200 and n["tags"] == ["a", "b"]
    m = SimpleNamespace(group=lambda i: str(n["id"]))
    st, e = N.api_note_op(ctx, m, {"op": "edit", "text": "new"})
    assert st == 200 and e["text"] == "new"
    st, x = N.api_note_op(ctx, m, {"op": "export"})
    assert st == 200 and x["markdown"].startswith("# t") and x["filename"].endswith(".md")
    assert N.api_notes(ctx, None, None, {"q": ["new"]})[1]["notes"][0]["id"] == n["id"]
    assert N.api_note_op(ctx, m, {"op": "delete"})[0] == 200
    assert N.api_note_op(ctx, m, {"op": "delete"})[0] == 404


# ---------------------------------------------------------------------- alerts
class Out:
    def __init__(self, up=True, ok=True):
        self.up, self.ok, self.got = up, ok, []

    def available(self):
        return self.up

    async def deliver(self, a):
        self.got.append(a)
        return self.ok


def engine(db, t, **outs):
    clock = [t]
    e = A.AlertEngine(db, outputs=outs, clock=lambda: clock[0])
    return e, clock


def test_quiet_window_wraps_midnight():
    d = lambda h, m=0: datetime(2026, 1, 1, h, m)
    assert A.in_quiet(d(23), "22:00", "07:00") and A.in_quiet(d(3), "22:00", "07:00")
    assert not A.in_quiet(d(7), "22:00", "07:00") and not A.in_quiet(d(12), "22:00", "07:00")
    assert A.in_quiet(d(13), "12:00", "14:00") and not A.in_quiet(d(14), "12:00", "14:00")


async def test_priority_channels_and_quiet_hours(db):
    popup, speech = Out(), Out()
    e, clock = engine(db, at(12), popup=popup, speech=speech)
    e.raise_("t", "low one", priority="low")
    e.raise_("t", "high one", priority="high")
    assert await e.flush() == 2
    assert [a["title"] for a in speech.got] == ["high one"] and [a["title"] for a in popup.got] == ["low one"]
    clock[0] = at(23)                                          # quiet hours: speech becomes a quiet popup
    e.raise_("t", "high at night", priority="high")
    e.raise_("t", "urgent at night", priority="urgent")
    await e.flush()
    assert popup.got[-1]["title"] == "high at night" and popup.got[-1]["quiet"] is True
    assert speech.got[-1]["title"] == "urgent at night"
    db.set_setting("alerts.urgent_in_quiet", False)
    e.raise_("t", "urgent 2", priority="urgent")
    await e.flush()
    assert popup.got[-1]["title"] == "urgent 2"


async def test_dedupe_and_rate_limit(db):
    popup = Out()
    e, clock = engine(db, at(12), popup=popup)
    a1 = e.raise_("t", "same", dedupe_key="k1")
    a2 = e.raise_("t", "same", dedupe_key="k1")
    assert a1["status"] == "pending" and a2["status"] == "suppressed" and a2["reason"] == "duplicate"
    db.set_setting("alerts.max_per_hour", 3)
    statuses = [e.raise_("t", f"n{i}")["status"] for i in range(4)]
    assert statuses == ["pending", "pending", "suppressed", "suppressed"]      # 1 + 2 fill the 3 per hour
    assert e.raise_("t", "urgent", priority="urgent")["status"] == "pending"   # urgent is exempt
    assert e.raise_("dashboard", "asked", user_requested=True)["status"] == "pending"
    clock[0] += 7 * 3600                                       # dedupe window and the hour are over
    assert e.raise_("t", "same", dedupe_key="k1")["status"] == "pending"


async def test_pending_queue_when_no_output_then_delivered(db):
    popup = Out(up=False)
    e, clock = engine(db, at(12), popup=popup)
    e.raise_("scheduler", "report ready", priority="normal")
    assert await e.flush() == 0 and len(e.pending()) == 1 and e.pending()[0]["attempts"] == 1
    popup.up = True                                            # the face connects
    assert await e.flush() == 1 and e.pending() == []
    assert e.recent(1)[0]["delivered_via"] == "popup"
    popup.up = False
    e.raise_("scheduler", "old", priority="normal")
    clock[0] += 25 * 3600
    await e.flush()
    assert e.recent(1)[0]["status"] == "expired"


async def test_pause_and_feature_off(db):
    popup = Out()
    e, clock = engine(db, at(12), popup=popup)
    e.pause(30, by="voice")
    e.raise_("t", "waits")
    e.raise_("t", "urgent", priority="urgent")
    await e.flush()
    assert [a["title"] for a in popup.got] == ["urgent"] and len(e.pending()) == 1
    clock[0] += 31 * 60
    await e.flush()
    assert popup.got[-1]["title"] == "waits"
    e.enabled = lambda: False                                  # proactive alerts switched off
    e.raise_("t", "news", kind="report")
    e.raise_("scheduler", "תזכורת", kind="reminder", priority="high")
    await e.flush()
    assert popup.got[-1]["title"] == "תזכורת" and [a["title"] for a in e.pending()] == ["news"]


async def test_readback_waits_for_a_voice_and_bypasses_quiet(db):
    popup, speech = Out(), Out(up=False)
    e, clock = engine(db, at(23), popup=popup, speech=speech)
    e.raise_("dashboard", "AI report", "full text", kind="readback", channel="speech", user_requested=True)
    await e.flush()
    assert popup.got == [] and len(e.pending()) == 1           # never shown silently instead of read
    speech.up = True
    await e.flush()
    assert speech.got[0]["text"] == "full text"
    assert A.speech_note(speech.got[0]).startswith("[report]")


async def test_broken_output_falls_back(db):
    class Boom(Out):
        async def deliver(self, a):
            raise RuntimeError("tts died")
    popup = Out()
    e, _ = engine(db, at(12), speech=Boom(), popup=popup)
    e.raise_("t", "x", priority="high")
    assert await e.flush() == 1 and popup.got[0]["title"] == "x"


def test_alert_settings_validation(db):
    e = A.AlertEngine(db)
    st = e.update_settings({"quiet_start": "23:30", "delivery": {"normal": "speech"}}, by="dashboard")
    assert st["alerts.quiet_start"] == "23:30" and st["alerts.delivery"]["normal"] == "speech"
    assert st["alerts.delivery"]["low"] == "popup"
    for bad in ({"quiet_start": "25:00"}, {"delivery": {"normal": "email"}}, {"nope": 1}):
        with pytest.raises(ValueError):
            e.update_settings(bad, by="dashboard")


# ---------------------------------------------------------------------- autonomy
class Feats:
    def __init__(self):
        self.on = {"music": True}
        self.sets = []

    def owner(self, skill, tool):
        return "music" if tool.startswith("music_") else None

    def enabled(self, fid):
        return self.on.get(fid, True)

    def set(self, fid, on, by):
        self.on[fid] = on
        self.sets.append((fid, on, by))


def issue(fp, kind, severity="low", count=3):
    return {"fingerprint": fp, "kind": kind, "severity": severity, "count": count, "status": "open",
            "title": f"{fp} title", "evidence": "{}"}


def auto(db, tmp_path, level=None):
    cfg = Config()
    a = AU.Autonomy(db, cfg=cfg, home=tmp_path, features=Feats(), alerts=A.AlertEngine(db))
    if level:
        a.set_level(level, by="dashboard")
    return a


def test_default_suggest_and_ask_needs_approval(db, tmp_path):
    a = auto(db, tmp_path)
    assert a.level() == "suggest_and_ask"
    made = a.sync([issue("false_wake", "wake"), issue("backend_stall", "stall", "high")])
    assert len(made) == 1 and made[0]["status"] == "proposed" and made[0]["applier"] == "wake_threshold"
    assert not (tmp_path / "config.toml").exists()                       # nothing changed yet
    assert a.alerts.pending()[0]["kind"] == "proposal"
    assert a.sync([issue("false_wake", "wake")]) == []                   # same issue: no duplicate proposal
    p = a.decide(made[0]["id"], True, by="dashboard")
    assert p["status"] == "applied" and a.cfg.voice.wake_threshold == pytest.approx(0.4)
    assert "wake_threshold = 0.4" in (tmp_path / "config.toml").read_text(encoding="utf-8")
    r = a.revert(p["id"], by="dashboard")
    assert r["status"] == "reverted" and "wake_threshold = 0.35" in (tmp_path / "config.toml").read_text("utf-8")
    acts = [x["action"] for x in db.audit_log()]
    assert {"proposal_new", "proposal_apply", "proposal_revert"} <= set(acts)


def test_observe_only_records_without_alert(db, tmp_path):
    a = auto(db, tmp_path)
    a.set_level("observe_only", by="voice")                              # lowering is allowed by voice
    made = a.sync([issue("false_wake", "wake")])
    assert made[0]["status"] == "observed" and a.alerts.pending() == []


def test_approved_low_risk_applies_only_granted_low_risk(db, tmp_path):
    a = auto(db, tmp_path, "approved_low_risk")
    made = a.sync([issue("false_wake", "wake"), issue("tool_fail:music_play", "tool", "high")])
    by_applier = {p["applier"]: p for p in made}
    assert by_applier["wake_threshold"]["status"] == "proposed"         # no grant yet -> still asks
    assert by_applier["feature_off"]["status"] == "proposed"
    a2 = auto(db, tmp_path / "b")
    (tmp_path / "b").mkdir()
    a2.set_grants(["wake_threshold"], by="dashboard")
    with pytest.raises(ValueError):
        a2.set_grants(["feature_off"], by="dashboard")                   # medium risk is never automatic
    db.x("DELETE FROM proposals")
    made = a2.sync([issue("false_wake", "wake"), issue("tool_fail:music_play", "tool", "high")])
    by_applier = {p["applier"]: p for p in made}
    assert by_applier["wake_threshold"]["status"] == "applied" and by_applier["wake_threshold"]["decided_by"] == \
        "autonomy"
    assert by_applier["feature_off"]["status"] == "proposed" and a2.features.sets == []


def test_more_autonomy_only_from_dashboard(db, tmp_path):
    a = auto(db, tmp_path)
    with pytest.raises(PermissionError):
        a.set_level("approved_low_risk", by="voice")
    with pytest.raises(PermissionError):
        a.set_grants(["wake_threshold"], by="voice")
    assert a.level() == "suggest_and_ask"


def test_autonomy_has_no_destructive_or_calendar_path():
    # closed list; config_setting / feature_on (self-check suggestions) are medium risk: never granted automatically
    assert set(AU.APPLIERS) == {"wake_threshold", "feature_off", "config_setting", "feature_on"}
    assert all(AU.APPLIERS[k].risk == "medium" for k in ("config_setting", "feature_on", "feature_off"))
    assert not any("delete" in k or "calendar" in k for k in AU.APPLIERS)
    import inspect
    src = inspect.getsource(AU)
    assert "registry.call" not in src                                   # autonomy never invokes Skills/tools


async def test_voice_proposal_decide_policy(db, tmp_path):
    a = auto(db, tmp_path)
    made = a.sync([issue("false_wake", "wake"), issue("tool_fail:music_play", "tool", "high")])
    reg = SkillRegistry(allowed_actions=AU.ALLOWED_ACTIONS)
    reg.register(AU.make_skill(a, reg))
    low = next(p for p in made if p["risk"] == "low")
    med = next(p for p in made if p["risk"] == "medium")
    r = await reg.call("proposal_decide", {"proposal_id": med["id"], "approve": True})
    assert r.code == "PROPOSAL_NEEDS_APPROVAL" and a.features.sets == []      # medium: on-screen approval
    assert (await reg.call("proposal_decide", {"proposal_id": low["id"], "approve": True})).ok
