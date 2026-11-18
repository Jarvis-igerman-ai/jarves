"""#660: RLIMIT_NPROC counts every task (threads too) of the REAL uid, so the headroom base must count the same."""
from types import SimpleNamespace

from jarves import isolation_exec


def _status(uid: int, threads: int) -> str:
    return f"Name:\tx\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\nThreads:\t{threads}\n"


def _fake(monkeypatch, pids, owner, status):
    """A fake /proc: `owner` is the directory owner, `status` the /proc/<pid>/status text (missing = vanished)."""
    def listdir(p):
        if p == "/proc":
            return pids
        raise FileNotFoundError(p)

    def fake_open(p, *a, **k):
        if p not in status:
            raise FileNotFoundError(p)
        import io
        return io.StringIO(status[p])
    monkeypatch.setattr(isolation_exec, "os", SimpleNamespace(getuid=lambda: 1000, listdir=listdir,
                                                              stat=lambda p: SimpleNamespace(st_uid=owner[p])))
    monkeypatch.setattr(isolation_exec, "open", fake_open, raising=False)


def test_user_procs_counts_threads_not_only_processes(monkeypatch):
    owner = {"/proc/1": 1000, "/proc/2": 1000, "/proc/3": 1000, "/proc/9": 0}
    status = {"/proc/1/status": _status(1000, 1), "/proc/2/status": _status(1000, 4),
              "/proc/3/status": _status(1000, 2), "/proc/9/status": _status(0, 50)}   # pid 9: someone else's
    _fake(monkeypatch, ["1", "2", "3", "self", "9", "77"], owner, status)                 # pid 77 vanished
    assert isolation_exec._user_procs() == 1 + 4 + 2      # 3 processes but 7 tasks


def test_user_procs_counts_by_real_uid_not_directory_owner(monkeypatch):
    """/proc/<pid> is owned by the EFFECTIVE uid, but the kernel charges RLIMIT_NPROC to the REAL uid. A process of
    the user running a setuid helper (euid root) was left out of the count, so the limit could land below the real
    task count and the approved 'echo | grep' could not fork: sh exit 2 (#660)."""
    owner = {"/proc/1": 1000, "/proc/4": 0, "/proc/5": 1000}
    status = {"/proc/1/status": _status(1000, 1), "/proc/4/status": _status(1000, 40),
              "/proc/5/status": "Uid:\tgarbage\n"}                                # unreadable fields: count 1
    _fake(monkeypatch, ["1", "4", "5"], owner, status)
    assert isolation_exec._user_procs() == 1 + 40 + 1

