"""Hidden-mode indicator (runtime/indicator.py, tray icon/tooltip) and idle stop (voice/live.py _idle_stop_due).
Hermetic: fake clock, fake dot backend, no tray library, no display."""
import pytest

from jarves import config as config_mod
from jarves.runtime import indicator, tray
from jarves.voice import live as live_mod


def view(**kw):
    v = {"core": {"state": "running"}, "mic": {"muted": False}, "locale": "en", "display": {"state": "hidden"},
         "voice": {"live_session": False, "connecting": False, "speaking": False, "thinking": False, "error": False}}
    for k, x in kw.items():
        if k in v["voice"]:
            v["voice"][k] = x
        else:
            v[k] = x
    return v


def test_state_mapping():
    assert indicator.state_of(view()) == "waiting"
    assert indicator.state_of(view(live_session=True)) == "listening"
    assert indicator.state_of(view(live_session=True, thinking=True)) == "thinking"
    assert indicator.state_of(view(live_session=True, thinking=True, speaking=True)) == "speaking"
    assert indicator.state_of(view(mic={"muted": True})) == "muted"
    assert indicator.state_of(view(error=True)) == "error"
    assert indicator.state_of(view(core={"state": "stopped"})) == "error"
    assert set(indicator.STATES) == set(indicator.STYLE)
    shapes = {indicator.STYLE[s][1] for s in indicator.STATES}
    assert len(shapes) == len(indicator.STATES)          # colour-blind safe: every state has its own shape


def test_tooltip_in_ui_language():
    assert "waiting" in tray.tooltip(view())
    he = tray.tooltip(view(locale="he", live_session=True))
    assert "מקשיב" in he and len(he) <= 120


def test_icon_differs_per_state():
    pytest.importorskip("PIL")
    raw = {s: tray.icon_image(view(**({"live_session": True} if s in ("listening", "thinking", "speaking") else {})
                                  | ({"thinking": True} if s == "thinking" else {})
                                  | ({"speaking": True} if s == "speaking" else {})
                                  | ({"mic": {"muted": True}} if s == "muted" else {})
                                  | ({"error": True} if s == "error" else {}))).tobytes()
           for s in indicator.STATES}
    assert len(set(raw.values())) == len(indicator.STATES)


class FakeDot:
    def __init__(self):
        self.calls = []

    def show(self, state, rgb):
        self.calls.append(("show", state))

    def hide(self):
        self.calls.append(("hide",))

    def close(self):
        self.calls.append(("close",))


def test_dot_only_while_conversation_open_and_window_hidden():
    be, on = FakeDot(), [True]
    dot = indicator.ListeningDot(be, lambda: on[0])
    dot.update(view())
    assert be.calls == []                                           # waiting: nothing shown
    dot.update(view(live_session=True))
    dot.update(view(live_session=True))                             # unchanged: no repeat
    dot.update(view(live_session=True, speaking=True))
    assert be.calls == [("show", "listening"), ("show", "speaking")]
    dot.update(view(live_session=True, display={"state": "shown"}))  # window shown: the dot goes away
    dot.update(view(live_session=True))
    dot.update(view())
    assert be.calls[-3:] == [("hide",), ("show", "listening"), ("hide",)]
    on[0] = False
    dot.update(view(live_session=True))
    assert be.calls[-1] == ("hide",)                                # setting off: never shown
    dot.stop()
    assert be.calls[-1] == ("close",)


def test_dot_backend_failure_is_swallowed():
    class Boom(FakeDot):
        def show(self, *a):
            raise RuntimeError("no display")
    indicator.ListeningDot(Boom()).update(view(live_session=True))


def test_setting_range():
    assert config_mod.VoiceConfig().idle_stop_s == 45
    for ok in (0, 15, 300):
        assert config_mod.VoiceConfig(idle_stop_s=ok).idle_stop_s == ok
    for bad in (1, 14, 301):
        with pytest.raises(Exception):
            config_mod.VoiceConfig(idle_stop_s=bad)


class Clock:
    t = 1000.0


@pytest.fixture
def lc(monkeypatch):
    monkeypatch.setattr(live_mod.time, "monotonic", lambda: Clock.t)
    Clock.t = 1000.0
    o = object.__new__(live_mod.SessionLifecycle)
    o.idle_disconnect_s, o.idle_stop_fn = 180, lambda: 45.0
    o._backend_busy, o._tools_running, o._confirm_pending = False, 0, False
    o._speaking_until, o._last_user_voice, o._t_ready, o._last_activity = 0.0, 0.0, 900.0, 900.0
    o.greeting, o._first_audio, o.audio_io = None, None, None
    return o


def test_idle_stop_after_quiet(lc):
    Clock.t = 900 + 44
    assert not lc._idle_stop_due()
    Clock.t = 900 + 46
    assert lc._idle_stop_due()


def test_idle_stop_off_cases(lc):
    Clock.t = 2000.0
    lc.idle_stop_fn = lambda: 0.0                    # setting 0 = never, or the window is shown
    assert not lc._idle_stop_due()
    lc.idle_stop_fn = lambda: 200.0                  # never later than the normal idle limit's own rule
    assert not lc._idle_stop_due()
    lc.idle_stop_fn = lambda: 45.0
    assert lc._idle_stop_due()
    lc.idle_stop_fn = None
    assert not lc._idle_stop_due()


@pytest.mark.parametrize("attr,val", [("_backend_busy", True), ("_tools_running", 1), ("_confirm_pending", True)])
def test_never_during_tool_answer_or_approval(lc, attr, val):
    Clock.t = 2000.0
    setattr(lc, attr, val)
    assert not lc._idle_stop_due()


def test_never_while_speaking_or_mid_utterance_or_audio_queued(lc):
    Clock.t = 2000.0
    lc._speaking_until = 2001.0
    assert not lc._idle_stop_due()
    lc._speaking_until, lc._last_user_voice = 0.0, 1999.5
    assert not lc._idle_stop_due()
    lc._last_user_voice = 0.0
    lc.audio_io = type("A", (), {"queued_s": staticmethod(lambda: 2.0)})()
    assert not lc._idle_stop_due()


def test_speech_end_restarts_the_quiet_timer(lc):
    Clock.t = 1000.0
    lc._speaking_until = 990.0                       # answer ended 10 s ago: quiet counts from then, not from 900
    assert not lc._idle_stop_due()
    Clock.t = 990 + 46
    assert lc._idle_stop_due()


def test_never_inside_greeting_pending_window(lc):
    import threading
    Clock.t = 2000.0
    lc.greeting, lc._first_audio, lc._t_ready = "hi", threading.Event(), 1995.0
    lc._last_activity = 0.0
    assert not lc._idle_stop_due()


def test_dot_retries_a_transient_tcl_init_error(monkeypatch):
    """#520 (2): the approval popup retries Windows' transient Tcl init errors; the dot's own Tk() did not, so one
    flaky 'couldn't read file auto.tcl' read left the dot dead for the whole run. It uses the popup's retry now."""
    import sys
    import types
    from unittest import mock

    class TclError(Exception):
        pass
    tries = []

    def make_root():
        tries.append(1)
        if len(tries) == 1:
            raise TclError('couldn\'t read file "auto.tcl"')
        return mock.MagicMock()
    fake = types.SimpleNamespace(Tk=make_root, TclError=TclError, Canvas=mock.MagicMock())
    monkeypatch.setitem(sys.modules, "tkinter", fake)
    from jarves import approval_popup
    monkeypatch.setattr(approval_popup.time, "sleep", lambda s: None)
    monkeypatch.setattr(indicator.sys, "platform", "linux")
    logged = []
    monkeypatch.setattr(indicator.logs, "debug",
                        lambda *a, **k: logged.extend(v for v in (*a, *k.values()) if v == "DOT_UNAVAILABLE"))
    indicator.TkDotBackend()._run()
    assert len(tries) == 2 and "DOT_UNAVAILABLE" not in logged
    fake.Tk = mock.Mock(side_effect=TclError("no display name and no $DISPLAY"))     # not transient: no retry
    indicator.TkDotBackend()._run()
    assert fake.Tk.call_count == 1 and logged == ["DOT_UNAVAILABLE"]
    fake.Tk = mock.Mock(side_effect=TclError("couldn't read file auto.tcl"))      # transient every time: 5 tries
    indicator.TkDotBackend()._run()
    assert fake.Tk.call_count == 5 and logged == ["DOT_UNAVAILABLE"] * 2
