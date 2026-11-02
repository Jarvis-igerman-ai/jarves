"""#660: RLIMIT_NPROC counts threads, so the headroom base must count tasks, not only processes."""
from types import SimpleNamespace

from jarves import isolation_exec


def test_user_procs_counts_threads_not_only_processes(monkeypatch):
    tree = {"/proc": ["1", "2", "3", "self", "9"], "/proc/1/task": ["1"], "/proc/2/task": ["2", "20", "21", "22"],
            "/proc/3/task": ["3", "30"]}                  # pid 9: someone else's; pid 1 has one thread
    owner = {"/proc/1": 1000, "/proc/2": 1000, "/proc/3": 1000, "/proc/9": 0}

    def listdir(p):
        if p in tree:
            return tree[p]
        raise FileNotFoundError(p)

    fake = SimpleNamespace(getuid=lambda: 1000, listdir=listdir, stat=lambda p: SimpleNamespace(st_uid=owner[p]))
    monkeypatch.setattr(isolation_exec, "os", fake)
    assert isolation_exec._user_procs() == 1 + 4 + 2      # 3 processes but 7 tasks
