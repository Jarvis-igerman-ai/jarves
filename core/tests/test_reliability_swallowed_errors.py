"""JAR-60: failures that used to be swallowed silently now leave a bounded structured log line (hermetic)."""
from __future__ import annotations

import types

from jarves import logs
from jarves.voice import delegation_client as dc
from jarves.voice import wake_confirm as wc


def _capture(monkeypatch, name):
    seen = []
    monkeypatch.setattr(logs, name, lambda comp, code, msg, **f: seen.append((comp, code, f)))
    return seen


def _ai():
    return types.SimpleNamespace(delegation_mode="client")


async def test_wake_confirm_warm_failure_is_logged(monkeypatch):
    class Confirm:
        def confirm(self, audio):
            raise RuntimeError("cold")
    seen = _capture(monkeypatch, "warn")
    obj = wc.ConfirmingDetector.__new__(wc.ConfirmingDetector)
    obj.confirm = Confirm().confirm
    await obj._warm()
    assert [(a, c) for a, c, _ in seen] == [("voice", "WAKE_CONFIRM_WARM_FAILED")]
    assert seen[0][2]["error_type"] == "RuntimeError"


async def test_audio_close_failure_is_logged_and_session_still_ends(monkeypatch):
    from jarves.voice.live import SessionLifecycle

    class Audio:
        def close(self):
            raise OSError("device gone")

    ended = []

    async def on_end(reason):
        ended.append(reason)

    s = SessionLifecycle()
    s._init_lifecycle(on_end, None, None)
    s.audio_io = Audio()
    flush = types.SimpleNamespace(cancelled=False)
    flush.cancel = lambda: setattr(flush, "cancelled", True)
    s._flush_task = flush
    seen = _capture(monkeypatch, "debug")
    await s._finalize("user")                   # must not raise; the steps after audio close still run
    assert [(a, c) for a, c, _ in seen] == [("voice", "VOICE_AUDIO_CLOSE_FAILED")]   # _log_area resolved
    assert seen[0][2]["error_type"] == "OSError"
    assert flush.cancelled is True
    assert ended == ["user"]


def test_state_save_failure_is_logged_once_per_outage(tmp_path, monkeypatch):
    st = dc.DelegationState(tmp_path, _ai())
    seen = _capture(monkeypatch, "warn")
    real = type(tmp_path).replace
    state = {"broken": True}

    def replace(self, t):
        if state["broken"]:
            raise PermissionError("x")
        return real(self, t)
    monkeypatch.setattr(type(tmp_path), "replace", replace)
    st.save()                                   # must not raise
    st.save()
    assert [(a, c) for a, c, _ in seen] == [("delegation", "DELEG_STATE_SAVE_FAILED")]
    assert seen[0][2] == {"error_type": "PermissionError"}
    state["broken"] = False
    st.save()                                   # recovered: the next outage is logged again
    state["broken"] = True
    st.save()
    assert [c for _, c, _ in seen] == ["DELEG_STATE_SAVE_FAILED"] * 2


def test_health_callback_failure_is_logged_once_per_outage(tmp_path, monkeypatch):
    state = {"broken": True}

    def cb(*a):
        if state["broken"]:
            raise RuntimeError("cb")
    st = dc.DelegationState(tmp_path, _ai(), health=cb)
    seen = _capture(monkeypatch, "warn")
    st._health("ok", "d", "")                   # must not raise
    st._health("ok", "d", "")
    assert [(a, c) for a, c, _ in seen] == [("delegation", "DELEG_HEALTH_CB_FAILED")]
    assert seen[0][2] == {"error_type": "RuntimeError"}
    state["broken"] = False
    st._health("ok", "d", "")
    state["broken"] = True
    st._health("ok", "d", "")
    assert [c for _, c, _ in seen] == ["DELEG_HEALTH_CB_FAILED"] * 2
