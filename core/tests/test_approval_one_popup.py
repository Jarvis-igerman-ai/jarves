"""#352: one approval popup. The tray toast is skipped while the approval window is shown; it carries the JARVES identity."""
from types import SimpleNamespace

from jarves import approvals as appr
from jarves.runtime import tray


class _Runtime:
    def __init__(self) -> None:
        self.tray = object()
        self.said: list[str] = []

    def _notify(self, text: str) -> None:
        self.said.append(text)


def _a(shown):
    return SimpleNamespace(shown=list(shown), title="anydo_create")


def test_no_toast_when_the_window_is_shown(monkeypatch):
    monkeypatch.setattr(tray, "pc_locked", lambda: False)
    ch = appr.TrayChannel(_Runtime())
    assert ch.available(_a(["popup"])) is False


def test_toast_when_the_window_could_not_open(monkeypatch):
    monkeypatch.setattr(tray, "pc_locked", lambda: False)
    assert appr.TrayChannel(_Runtime()).available(_a([])) is True


def test_toast_when_the_pc_is_locked(monkeypatch):
    monkeypatch.setattr(tray, "pc_locked", lambda: True)
    assert appr.TrayChannel(_Runtime()).available(_a(["popup"])) is True


def test_no_tray_no_toast():
    rt = _Runtime()
    rt.tray = None
    assert appr.TrayChannel(rt).available(_a([])) is False


def test_identity_is_jarves_not_python(monkeypatch):
    assert tray.APP_ID.startswith("JARVES")
    if not __import__("sys").platform == "win32":
        assert tray.set_app_identity() is False
        assert tray.pc_locked() is False


def test_lock_detection_fails_open(monkeypatch):
    monkeypatch.setattr(tray, "pc_locked", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert appr.TrayChannel(_Runtime()).available(_a(["popup"])) is True


def test_toast_fires_if_the_popup_process_crashes():
    import threading
    rt = _Runtime()
    rt.tray = object()
    tc = appr.TrayChannel(rt)
    a = SimpleNamespace(shown=["popup"], title="t")
    svc = SimpleNamespace(_lock=threading.Lock(), _pending={"i": (a, None, None)}, channels=[tc])
    appr.PopupChannel._fallback_toast(SimpleNamespace(svc=svc), "i")
    assert rt.said and "t" in rt.said[0]
    rt.said.clear()
    svc._pending.clear()                       # already answered elsewhere: no toast
    appr.PopupChannel._fallback_toast(SimpleNamespace(svc=svc), "i")
    assert rt.said == []
