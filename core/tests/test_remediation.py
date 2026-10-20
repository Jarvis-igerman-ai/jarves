"""SW-V4 O (#17): auto-remediation ONLY through approvals. Every remedy of the closed list runs end-to-end on a real
Core (tmp home, fakes for audio/STT/TTS/music) through the Core's own ApprovalService, with its pre-check and
post-check; a denial does nothing; only "restart the face link" may be granted for automatic use."""
from __future__ import annotations

import contextvars
import json

import pytest

from jarves import approvals as appr
from jarves.diag import remediation
from jarves.diag.service import HealthService


class AnswerChannel:
    """An approval channel that answers every request (approve or deny), like the owner on the popup."""
    name = "dashboard"

    def __init__(self, svc, approve: bool):
        self.svc, self.approve, self.asked = svc, approve, []

    def available(self, a):
        return True

    def open(self, a):
        self.asked.append(a.title)
        self.svc.loop.call_soon(lambda: self.svc.answer(a.id, self.approve, channel="dashboard"),
                                context=contextvars.Context())     # the owner answers outside any tool

    def close(self, a):
        return None


@pytest.fixture
def svc(core):
    hs = core.ops.health_diag
    assert isinstance(hs, HealthService)
    return hs


def answer(core, approve: bool) -> AnswerChannel:
    ch = AnswerChannel(core.approvals, approve)
    core.approvals.channels[:] = [ch]
    core.approvals.rate_limit = (100, 60.0)       # many remedies in one test (the 6/min limit has its own tests)
    return ch


async def test_every_remedy_runs_through_approvals_with_post_check(core, svc, monkeypatch, home):
    ch = answer(core, True)
    # make each remedy possible on this test Core
    (home / "cache").mkdir(exist_ok=True)
    (home / "cache" / "speech_abc.wav").write_bytes(b"RIFF")
    core.voice_mode = "live"                                     # pretend GPT-Live was in use
    custom = home / "wake" / "mine.npz"
    custom.parent.mkdir()
    custom.write_bytes(b"npz")
    (home / "identity.json").write_text(json.dumps({"wake": {"model": str(custom), "phrase": "hey max"}}))
    ready = {"x": False}
    from jarves import feature_setup, features as feat, updates
    monkeypatch.setattr(feat.CATALOG["music"], "probe", lambda c: (ready["x"], "Pear missing"))
    core.features.invalidate("music")

    def prepare(fid, features, home_):
        ready["x"] = True
        return True, "installed"
    monkeypatch.setattr(feature_setup, "prepare", prepare)
    monkeypatch.setattr(updates, "rollback", lambda h: "rolled back to 4.0.0", raising=False)
    monkeypatch.setattr(updates, "can_rollback", lambda h: (True, "previous version kept"), raising=False)
    results = {}
    for rid in remediation.REMEDIES:
        args = {"feature": "music"} if rid == "rerun_feature_setup" else {}
        results[rid] = await svc.remediate(rid, by="test", finding=f"f:{rid}", args=args)
    blocked = {"restart_bridges"}                                # no bridge in this build (packages E / F)
    for rid, r in results.items():
        if rid in blocked:
            assert r["status"] == "precheck_failed" and not r["approved"], r
        else:
            assert r["status"] == "done" and r["approved"] and not r["automatic"], (rid, r)
            assert r["postcheck"], rid
    assert len(ch.asked) == len(remediation.REMEDIES) - len(blocked)   # every action asked first
    assert not list((home / "cache").glob("speech_*.wav"))
    assert core.voice_mode == "push_to_talk"
    from jarves.identity import Identity
    assert Identity(home, core.cfg).wake()["model"] == "hey_jarvis" and custom.exists()
    hist = appr.read_history(home, 100)
    assert sum(h["decision"] == "approved" for h in hist) >= len(ch.asked)


async def test_deny_does_nothing(core, svc, home):
    ch = answer(core, False)
    (home / "cache").mkdir(exist_ok=True)
    f = home / "cache" / "speech_keep.wav"
    f.write_bytes(b"RIFF")
    port_before = core.link._server
    r1 = await svc.remediate("clear_speech_cache", by="test")
    r2 = await svc.remediate("restart_face_link", by="test")
    assert r1["status"] == r2["status"] == "denied" and len(ch.asked) == 2
    assert f.exists() and core.link._server is port_before          # nothing touched
    assert {h["decision"] for h in appr.read_history(home, 10)} == {"denied"}


async def test_only_the_face_link_may_be_granted_for_automatic_use(core, svc):
    ch = answer(core, False)                                         # the owner would say no if asked
    r = await svc.remediate("restart_face_link", by="monitor", automatic=True)
    assert r["status"] == "denied" and ch.asked                       # not granted yet: it asks
    svc.db.set_setting("health.auto_face_link_restart", True, actor="owner")
    ch.asked.clear()
    r = await svc.remediate("restart_face_link", by="monitor", automatic=True)
    assert r["status"] == "done" and r["automatic"] and not ch.asked
    r = await svc.remediate("restart_audio_device", by="monitor", automatic=True)
    assert r["status"] == "denied" and ch.asked == ["Re-open the audio devices"]   # everything else always asks
    svc.db.set_setting("autonomy.grants", ["config_setting", "restart_audio_device"], actor="owner")
    ch.asked.clear()
    r = await svc.remediate("restart_audio_device", by="monitor", automatic=True)
    assert r["status"] == "denied" and ch.asked                       # autonomy grants never skip the question


async def test_voice_tool_asks_and_unknown_is_refused(core, svc):
    ch = answer(core, True)
    r = await core.registry.call("health_remediate", {"remedy": "clear_guide_cache"}, origin="model")
    assert r.ok and r.data["status"] == "done" and ch.asked
    r = await core.registry.call("health_remediate", {"remedy": "format_disk"}, origin="model")
    assert not r.ok and r.code == "HEALTH_REMEDY_UNKNOWN"


async def test_no_approval_channel_means_denied(home):
    from jarves.store import Store
    st = Store(home / "store.db")
    try:
        c = remediation.RemedyCtx(home, st, ask=None)
        r = await remediation.run("clear_speech_cache", c, by="test")
        assert r["status"] == "denied" and r["result"] == "no approval channel"
        with pytest.raises(KeyError):
            await remediation.run("rm_rf", c, by="test")
        assert {x["id"] for x in remediation.catalog()} == set(remediation.REMEDIES)
        assert [x["id"] for x in remediation.catalog() if x["grantable"]] == ["restart_face_link"]
    finally:
        st.close()
