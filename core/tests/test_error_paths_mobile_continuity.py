"""Error/edge paths for mobile.py and continuity.py: malformed input, None config, NaN/Infinity. Hermetic, fake clock."""
from __future__ import annotations

import json
import math

import pytest

from jarves import mobile
from jarves.continuity import Continuity
from jarves.mobile import MobileAccess

@pytest.fixture(autouse=True)
def _full_text():
    from jarves import logs
    before = logs.privacy()
    logs.set_privacy(False)
    yield
    logs.set_privacy(before)


NOW = 1_800_000_000.0
BAD_NUMBERS = [float("nan"), float("inf"), float("-inf"), "nan", "inf", "abc", None, [], {}]


class _Goals:
    def set_status(self, gid, status, by=""):
        raise KeyError(gid)

    def set_step(self, sid, done, by=""):
        raise KeyError(sid)


class _Bridge:
    def __init__(self, goals):
        self._g = goals

    def goals(self):
        return self._g


def _mob(tmp_path, goals=None):
    m = MobileAccess(tmp_path, clock=lambda: NOW)
    m.bridge = _Bridge(goals) if goals is not None else None
    return m


# ---- mobile.py ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("token", [None, "", "abc", ".", "a.", ".b", "x" * 300, "a.b.c"])
def test_authenticate_rejects_malformed_tokens(tmp_path, token):
    assert MobileAccess(tmp_path).authenticate(token) is None


@pytest.mark.parametrize("code", [None, "", "abc", "0" * 50, 12.5])
def test_pair_without_active_code_or_with_junk_returns_none(tmp_path, code):
    m = MobileAccess(tmp_path, clock=lambda: NOW)
    assert m.pair(code, None, "10.0.0.5") is None


def test_pair_with_none_name_defaults(tmp_path):
    m = MobileAccess(tmp_path, clock=lambda: NOW)
    code = m.new_code()["code"]
    assert m.pair(code, None, "10.0.0.5")["name"] == "phone"


def test_goal_action_without_bridge_is_503(tmp_path):
    assert mobile.goal_action(_mob(tmp_path), {"op": "status"})[0] == 503


def test_goal_action_unknown_op_and_bad_status(tmp_path):
    m = _mob(tmp_path, _Goals())
    assert mobile.goal_action(m, {"op": None})[0] == 400
    assert mobile.goal_action(m, {"op": "status", "status": "deleted"})[0] == 400
    assert mobile.goal_action(m, {"op": "status", "status": "done", "goal": 1})[0] == 404


@pytest.mark.parametrize("bad", BAD_NUMBERS)
def test_goal_action_bad_ids_never_raise(tmp_path, bad):
    m = _mob(tmp_path, _Goals())
    for d in ({"op": "status", "status": "done", "goal": bad}, {"op": "step_set", "step": bad}):
        st, _ = mobile.goal_action(m, d)
        assert st in (400, 404)


@pytest.mark.parametrize("bad", BAD_NUMBERS)
def test_signals_snooze_bad_minutes_never_raise(tmp_path, bad):
    st, body = mobile.signals_action(_mob(tmp_path), {"op": "snooze", "minutes": bad}, "phone1")
    assert st in (200, 400)
    if st == 200:
        assert math.isfinite(body["snoozed_until"]) and body["snoozed_until"] >= NOW + 60


def test_signals_unknown_op(tmp_path):
    assert mobile.signals_action(_mob(tmp_path), {}, "p")[0] == 400


def test_page_of_and_network_ok_odd_paths():
    assert mobile.page_of("") == "" and mobile.page_of("/api/m/") == "" and mobile.page_of("/api/m") == ""
    assert not mobile.network_ok("not-an-ip", lan=True, mobile_on=True, path="/m")


# ---- continuity.py -----------------------------------------------------------------------------------------
def _c(tmp_path):
    return Continuity(tmp_path, clock=lambda: NOW)


def test_execute_garbage_age_is_not_stale(tmp_path):
    c = _c(tmp_path)
    for age in ("abc", None, [], float("nan")):
        assert c.stale(age) is False
    assert c.stale(float("inf")) is True


def test_execute_runs_without_cid_and_does_not_store_500(tmp_path):
    c = _c(tmp_path)
    assert c.execute("d", None, "k", lambda: (200, {"a": 1}))[2] is False
    calls = []
    c.execute("d", "x", "k", lambda: (calls.append(1) or 500, {}))
    c.execute("d", "x", "k", lambda: (calls.append(1) or 200, {}))
    assert len(calls) == 2


def test_add_turn_none_text_and_overlong(tmp_path):
    c = _c(tmp_path)
    c.add_turn("d", "user", None)
    c.add_turn("d", "user", "x" * 9000)
    assert [len(t["text"]) for t in c.turns()] == [0, 4000]


def test_post_notice_none_text_truncates(tmp_path):
    c = _c(tmp_path)
    assert c.post_notice("a", "k", None) is not None
    c.post_notice("b", "k", "y" * 5000)
    assert len(c.fetch_notices("desktop")[1]["text"]) == 1000


def test_ack_unknown_notice_is_false(tmp_path):
    assert _c(tmp_path).ack_notice("desktop", 999) is False


def test_snooze_nan_clamps_to_minimum(tmp_path):
    c = _c(tmp_path)
    assert c.snooze(float("nan")) == NOW + 60
    assert c.snooze(float("inf")) == NOW + 24 * 3600
    assert c.snooze(-5) == NOW + 60


def test_configure_signals_clamps_and_none_leaves_alone(tmp_path):
    c = _c(tmp_path)
    s = c.configure_signals(hourly=10**6, daily=-3, cost_cap=None)
    assert (s["hourly"], s["daily"], s["daily_cost_cap"]) == (20, 0, 0)
    assert c.configure_signals()["on"] is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc"])
def test_configure_signals_bad_numbers_raise_only_numeric_errors(tmp_path, bad):
    with pytest.raises((ValueError, OverflowError)):
        _c(tmp_path).configure_signals(hourly=bad)


def test_emit_with_signals_off_and_bad_quiet(tmp_path):
    assert _c(tmp_path).emit("k", "r", "t") == "off"


def test_on_goal_event_tolerates_missing_updated(tmp_path):
    c = _c(tmp_path)
    c.configure_signals(on=True)
    assert c.on_goal_event({"id": 1, "title": "g", "updated": None}, "stalled") == "queued"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc", None, [1]])
@pytest.mark.parametrize("op", [{"op": "config", "hourly": 0}, {"op": "snooze"}, {"op": "ack"}])
def test_api_continuity_op_bad_numbers_give_400_not_500(tmp_path, bad, op):
    class Ctx:
        home = tmp_path
        _continuity = _c(tmp_path)
    key = {"config": "hourly", "snooze": "minutes", "ack": "id"}[op["op"]]
    st, _ = _call(Ctx, {**op, key: bad})
    assert st in (200, 400)


def test_api_continuity_op_non_object_body(tmp_path):
    class Ctx:
        home = tmp_path
        _continuity = _c(tmp_path)
    for body in (None, {}, {"op": None}, {"op": "bogus"}):
        assert _call(Ctx, body)[0] in (200, 400)
    assert _call(Ctx, ["op"])[0] == 400


def _call(ctx, body):
    from jarves import continuity
    return continuity.api_continuity_op(ctx, None, body)
