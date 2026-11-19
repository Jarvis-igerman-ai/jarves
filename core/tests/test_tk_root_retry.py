"""#520: the listening dot and the approval popup share one transient Tcl-init retry."""
from types import SimpleNamespace

import pytest

from jarves.tkutil import new_tk_root


class _TclError(Exception):
    pass


def _tk(errors):
    calls = []

    def Tk():
        calls.append(1)
        if errors:
            raise _TclError(errors.pop(0))
        return "root"
    return SimpleNamespace(Tk=Tk, TclError=_TclError), calls


def test_transient_tcl_init_error_is_retried():
    tk, calls = _tk(['invalid command name "tcl_findLibrary"', "couldn't read file auto.tcl"])
    assert new_tk_root(tk, sleep=lambda s: None) == "root" and len(calls) == 3


def test_other_errors_and_exhausted_retries_raise():
    tk, calls = _tk(["no display name"])
    with pytest.raises(_TclError):
        new_tk_root(tk, sleep=lambda s: None)
    assert len(calls) == 1
    tk, calls = _tk(["init.tcl"] * 5)
    with pytest.raises(_TclError):
        new_tk_root(tk, sleep=lambda s: None)
    assert len(calls) == 5


def test_zero_attempts_still_tries_once():
    tk, calls = _tk([])
    assert new_tk_root(tk, attempts=0) == "root" and len(calls) == 1


def test_listening_dot_survives_one_transient_tcl_error(monkeypatch):
    import sys
    from jarves.runtime.indicator import TkDotBackend
    errors, reached = ["couldn't read file auto.tcl"], []

    class Root:
        def __getattr__(self, name):
            return lambda *a, **k: 100 if name.startswith("winfo") else None

        def mainloop(self):
            reached.append(True)

    def Tk():
        if errors:
            raise _TclError(errors.pop(0))
        return Root()
    fake = SimpleNamespace(Tk=Tk, TclError=_TclError, Canvas=lambda *a, **k: Root())
    monkeypatch.setitem(sys.modules, "tkinter", fake)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("jarves.tkutil.time.sleep", lambda s: None)      # clock-free
    TkDotBackend()._run()
    assert reached == [True]          # before #520 the first TclError made the dot give up for the whole run


def test_popup_module_starts_in_its_own_process_like_the_core_launches_it(tmp_path):
    """The core starts the popup as `python -m jarves.approval_popup` with src on PYTHONPATH (approvals.PopupChannel).
    Its package-relative tkutil import must load there; --help exits before any Tk window (works headless)."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    from jarves import approvals
    src = str(Path(approvals.__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=src)
    p = subprocess.run([approvals._popup_python(), "-m", "jarves.approval_popup", "--help"], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and "--test-invoke" in p.stdout, p.stderr
