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


def test_state_save_failure_is_logged_not_swallowed(tmp_path, monkeypatch):
    st = dc.DelegationState(tmp_path, _ai())
    seen = _capture(monkeypatch, "warn")
    monkeypatch.setattr(type(tmp_path), "replace", lambda self, t: (_ for _ in ()).throw(PermissionError("x")))
    st.save()                                   # must not raise
    assert [(a, c) for a, c, _ in seen] == [("delegation", "DELEG_STATE_SAVE_FAILED")]
    assert seen[0][2]["error_type"] == "PermissionError"


def test_health_callback_failure_is_logged(tmp_path, monkeypatch):
    def boom(*a):
        raise RuntimeError("cb")
    st = dc.DelegationState(tmp_path, _ai(), health=boom)
    seen = _capture(monkeypatch, "warn")
    st._health("ok", "d", "")                   # must not raise
    assert [(a, c) for a, c, _ in seen] == [("delegation", "DELEG_HEALTH_CB_FAILED")]
    assert seen[0][2]["error_type"] == "RuntimeError"


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
