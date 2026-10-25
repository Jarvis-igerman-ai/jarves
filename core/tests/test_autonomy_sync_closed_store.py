"""#430: AUTONOMY_SYNC_FAILED (sqlite ProgrammingError) when the history store was closed while a tick still ran."""
import asyncio
import sqlite3
import threading
import types

from jarves import logs, operations
from jarves.store import Store


class _Noop:
    async def check(self): pass
    async def flush(self): pass


def _ops(store, synced):
    ops = types.SimpleNamespace(
        _synced=0.0, cheer=_Noop(), alerts=_Noop(),
        core=types.SimpleNamespace(history=types.SimpleNamespace(store=store), home=None),
        autonomy=types.SimpleNamespace(sync=lambda issues: synced.append(issues)))
    async def _noop(): pass
    ops._signals_tick = _noop
    ops._connect_nudge = _noop
    return ops


def _tick(ops):
    asyncio.run(operations.Operations._after_tick(ops))


def _errors(monkeypatch):
    seen = []
    monkeypatch.setattr(logs, "error", lambda *a, **k: seen.append(a))
    return seen


def test_open_store_syncs(tmp_path, monkeypatch):
    st, synced = Store(tmp_path / "s.db"), []
    errs = _errors(monkeypatch)
    _tick(_ops(st, synced))
    assert synced == [[]] and not errs
    st.close()


def test_closed_store_is_skipped_quietly(tmp_path, monkeypatch):
    st, synced = Store(tmp_path / "s.db"), []
    errs = _errors(monkeypatch)
    st.close()
    st.close()                                   # idempotent
    assert st.closed
    _tick(_ops(st, synced))
    assert synced == [] and not errs


def test_close_race_between_check_and_query(tmp_path, monkeypatch):
    st, synced = Store(tmp_path / "s.db"), []
    errs = _errors(monkeypatch)
    real = st.issues
    def racing(status="open"):
        st.close()
        return real(status)
    st.issues = racing
    _tick(_ops(st, synced))
    assert synced == [] and not errs


def test_other_programming_error_still_logged(tmp_path, monkeypatch):
    st, synced = Store(tmp_path / "s.db"), []
    errs = _errors(monkeypatch)
    def boom(status="open"): raise sqlite3.ProgrammingError("bad")
    st.issues = boom
    _tick(_ops(st, synced))
    assert errs and errs[0][1] == "AUTONOMY_SYNC_FAILED"
    st.close()


def test_cross_thread_use_works(tmp_path):
    st, out = Store(tmp_path / "s.db"), []
    t = threading.Thread(target=lambda: out.append(st.issues("open")))
    t.start(); t.join()
    assert out == [[]]
    st.close()
