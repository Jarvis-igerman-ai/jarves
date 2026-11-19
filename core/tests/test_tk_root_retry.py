"""#520: the listening dot and the approval popup share one transient Tcl-init retry."""
from types import SimpleNamespace

import pytest

from jarves.approval_popup import new_tk_root


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


def test_listening_dot_uses_the_shared_retry():
    import inspect
    from jarves.runtime import indicator
    assert "new_tk_root(tk)" in inspect.getsource(indicator.TkDotBackend._run)
