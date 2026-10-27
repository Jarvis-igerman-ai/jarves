"""B4 (#399): the phone path to the pilot's live view. Fake session/driver and fake mob (features, rate, scheme); no browser, no network.
The real-browser phone test is test_mobile_browser_e2e.py."""
from __future__ import annotations

import pytest
from test_browser_takeover import Drv, mk  # noqa: F401

from jarves import browser_liveview as bl
from jarves import browser_pilot as bp
from jarves import features as feat
from jarves import mobile_browser as mb


class Feats:
    def __init__(self, *on):
        self.on = set(on)

    def active(self, fid):
        return fid in self.on


class Mob:
    """Shaped like the real MobileAccess: features come through mob.bridge.drive_features (mobile_drive._features)."""

    def __init__(self, *on, limit=10_000):
        from types import SimpleNamespace as NS
        self._f, self.hits, self.limit = Feats(*on), 0, limit
        self.bridge = NS(drive_features=lambda: self._f)

    def rate_ok(self, key, limit):
        self.hits += 1
        return self.hits <= self.limit


ALL = (mb.FEATURE, bl.FEATURE, bl.TAKEOVER)


@pytest.fixture
def world(tmp_path):
    v, s, d, t = mk(tmp_path)
    bl.set_active(v)
    yield v, s, d
    bl.set_active(None)


def call(mob, op, body=None, scheme="https"):
    return mb.handle(mob, "dev1", op, body or {}, scheme)


def test_the_switch_is_beta_off_by_default_and_needs_the_live_view():
    f = feat.CATALOG[mb.FEATURE]
    assert f.default_enabled is False and feat.stage_of(mb.FEATURE) == "beta" and "browser_liveview" in f.needs


def test_everything_is_refused_while_the_phone_switch_or_the_live_view_switch_is_off(world):
    for on in ((), (bl.FEATURE,), (mb.FEATURE,), (bl.TAKEOVER, bl.FEATURE)):
        st, body = call(Mob(*on), "start")
        assert st == 403 and body["code"] == "PHONE_BROWSER_OFF"


def test_plain_http_is_refused(world):
    st, body = call(Mob(*ALL), "start", scheme="http")
    assert st == 409 and body["code"] == "PHONE_BROWSER_NO_TLS"


def test_no_running_pilot_is_a_404_the_phone_cannot_start_one(tmp_path):
    bl.set_active(None)
    st, body = call(Mob(*ALL), "start")
    assert st == 404 and body["code"] == "PHONE_BROWSER_NO_SESSION"
    assert not hasattr(mb, "PilotService") and "pilot/start" not in open(mb.__file__, encoding="utf-8").read()


def test_watch_then_take_over_type_give_back_and_stop(world):
    v, s, d = world
    mob = Mob(*ALL)
    st, r = call(mob, "start")
    assert st == 200 and r["grant"]
    g = r["grant"]
    st, f = call(mob, "frame", {"grant": g})
    assert st == 200 and f["png_b64"] and f["state"] == bp.AGENT
    assert call(mob, "takeover", {"grant": g})[0] == 200 and s.state == bp.HUMAN
    assert call(mob, "input", {"grant": g, "kind": "type", "data": {"text": "s3cret-pw"}})[0] == 200
    assert call(mob, "input", {"grant": g, "kind": "click", "data": {"x": 5, "y": 6}})[0] == 200
    assert ("type", {"text": "s3cret-pw"}) in d.inputs and ("click", {"x": 5, "y": 6}) in d.inputs
    st, out = call(mob, "return", {"grant": g})
    assert st == 200 and out["state"] == bp.PAUSED
    st, out = call(mob, "return", {"grant": g, "resume": True})
    assert out["state"] == bp.AGENT
    assert call(mob, "stop")[0] == 200 and s.state == bp.STOPPED


def test_the_takeover_switch_is_needed_for_input(world):
    mob = Mob(mb.FEATURE, bl.FEATURE)                         # view yes, take over no
    v, s, d = world
    v.takeover_enabled = lambda: False                        # what PilotService sets from the browser_takeover switch
    g = call(mob, "start")[1]["grant"]
    st, body = call(mob, "takeover", {"grant": g})
    assert st == 403 and body["code"] == "LIVEVIEW_TAKEOVER_OFF" and d.inputs == [] and s.state == bp.AGENT


def test_a_wrong_or_missing_grant_is_refused(world):
    mob = Mob(*ALL)
    call(mob, "start")
    for body in ({}, {"grant": "nope"}, {"grant": 5}):
        st, out = call(mob, "frame", body)
        assert st == 403 and out["code"] == "LIVEVIEW_TOKEN"


def test_unknown_op_is_404_and_a_flood_is_rate_limited(world):
    assert call(Mob(*ALL), "launch")[0] == 404
    mob = Mob(*ALL, limit=2)
    call(mob, "start")
    call(mob, "start")
    st, out = call(mob, "start")
    assert st == 429 and out["code"] == "PHONE_BROWSER_RATE"


def test_errors_are_fixed_words_and_never_carry_typed_text(world):
    v, s, d = world
    mob = Mob(*ALL)
    g = call(mob, "start")[1]["grant"]
    call(mob, "takeover", {"grant": g})

    def boom(kind, data):
        raise RuntimeError("selector failed for " + data.get("text", ""))
    d.on_input = boom
    st, out = call(mob, "input", {"grant": g, "kind": "type", "data": {"text": "hunter2-xyz"}})
    assert st >= 400 and "hunter2-xyz" not in str(out)


def test_status_for_the_tab(world):
    st = mb.status(Mob(*ALL))
    assert st == {"enabled": True, "session": True, "state": bp.AGENT, "takeover": True}
    assert mb.status(Mob())["enabled"] is False
    bl.set_active(None)
    assert mb.status(Mob(*ALL))["session"] is False


def test_the_browser_scope_is_not_a_default_scope():
    from jarves import mobile
    assert "browser" in mobile.ALL_SCOPES and "browser" not in mobile.DEFAULT_SCOPES
