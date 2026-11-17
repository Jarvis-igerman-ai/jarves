"""NaN/Infinity and non-object input on phone goal/snooze and the dashboard continuity op. Hermetic, fake clock."""
from __future__ import annotations

import pytest

from jarves import continuity, mobile
from jarves.continuity import Continuity
from jarves.mobile import MobileAccess

NOW = 1_800_000_000.0
MIN_UNTIL = NOW + 60                 # snooze floor: 1 minute
MAX_UNTIL = NOW + 24 * 3600          # snooze ceiling: SNOOZE_MAX_MIN
NAN, INF = float("nan"), float("inf")


class _Goals:
    def set_status(self, gid, status, by=""):
        raise KeyError(gid)

    def set_step(self, sid, done, by=""):
        raise KeyError(sid)


class _Bridge:
    def goals(self):
        return _Goals()


def _mob(tmp_path):
    m = MobileAccess(tmp_path, clock=lambda: NOW)
    m.bridge = _Bridge()
    return m


def _ctx(tmp_path):
    class Ctx:
        home = tmp_path
        _continuity = Continuity(tmp_path, clock=lambda: NOW)
    return Ctx


# ---- mobile.py: phone goal / snooze --------------------------------------------------------------------
@pytest.mark.parametrize("bad", [NAN, INF, -INF, "inf", "nan"])
def test_goal_action_non_finite_ids_are_400(tmp_path, bad):
    m = _mob(tmp_path)
    assert mobile.goal_action(m, {"op": "status", "status": "done", "goal": bad})[0] == 400
    assert mobile.goal_action(m, {"op": "step_set", "step": bad})[0] == 400


@pytest.mark.parametrize(("bad", "until"), [(NAN, MIN_UNTIL), (INF, MAX_UNTIL), (-INF, MIN_UNTIL),
                                            ("inf", MAX_UNTIL), ("nan", MIN_UNTIL)])
def test_signals_snooze_non_finite_minutes_clamp(tmp_path, bad, until):
    assert mobile.signals_action(_mob(tmp_path), {"op": "snooze", "minutes": bad}, "phone1") == \
        (200, {"snoozed_until": until})


# ---- continuity.py ------------------------------------------------------------------------------------
def test_snooze_nan_clamps_to_minimum_and_inf_to_maximum(tmp_path):
    c = Continuity(tmp_path, clock=lambda: NOW)
    assert c.snooze(NAN) == MIN_UNTIL
    assert c.snooze(INF) == MAX_UNTIL
    assert c.snooze(-INF) == MIN_UNTIL


@pytest.mark.parametrize("bad", [NAN, INF, -INF])
@pytest.mark.parametrize(("op", "key"), [("config", "hourly"), ("ack", "id")])
def test_api_continuity_op_non_finite_int_is_400(tmp_path, bad, op, key):
    st, body = continuity.api_continuity_op(_ctx(tmp_path), None, {"op": op, key: bad})
    assert st == 400 and "convert float" in body["error"]


@pytest.mark.parametrize(("bad", "until"), [(NAN, MIN_UNTIL), (INF, MAX_UNTIL), (-INF, MIN_UNTIL)])
def test_api_continuity_op_snooze_non_finite_clamps(tmp_path, bad, until):
    assert continuity.api_continuity_op(_ctx(tmp_path), None, {"op": "snooze", "minutes": bad}) == \
        (200, {"snoozed_until": until})


@pytest.mark.parametrize("body", [["op"], "snooze", 5])
def test_api_continuity_op_non_object_body_is_400(tmp_path, body):
    assert continuity.api_continuity_op(_ctx(tmp_path), None, body) == (400, {"error": "JSON object expected"})
