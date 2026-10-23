"""projects.db: projects, tasks and their timeline (one SQLite file per profile home, WAL).

Kept apart from agents.db (live sessions) and store.db (conversations), so wiping one never wipes the others.
Task status machine: proposed -> todo -> ready -> running -> review -> done; side states blocked, failed, cancelled.
`ready` is derived by `promote()` (every dependency done); nothing else moves a task into or out of `running`.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

MODES = ("solo", "team")      # team = the agent may hand parts of the task to its own sub-agents
TASK_STATES = ("proposed", "todo", "ready", "running", "review", "done", "blocked", "failed", "cancelled")
OPEN_STATES = ("todo", "ready", "running", "review", "blocked")      # work that still needs doing
FINAL_STATES = ("done", "failed", "cancelled")
PROJECT_STATES = ("active", "paused", "done", "archived")
EVENTS_KEEP = 400
DEP_BLOCK = "dependency "                                             # reason prefix of a dependency block                                                     # timeline rows kept per task

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, path TEXT NOT NULL, goal TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'paused', brief TEXT NOT NULL DEFAULT '', check_cmd TEXT NOT NULL DEFAULT '[]',
    runners TEXT NOT NULL DEFAULT '[]', max_parallel INTEGER NOT NULL DEFAULT 0, base_branch TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, seq INTEGER NOT NULL, title TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '', acceptance TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '[]',
    deps TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'todo', runner TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '', used_runner TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
    branch TEXT NOT NULL DEFAULT '', worktree TEXT NOT NULL DEFAULT '', session_ref TEXT NOT NULL DEFAULT '',
    handoff TEXT NOT NULL DEFAULT '', handoff_kind TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '[]', cost_usd REAL NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0, pid INTEGER NOT NULL DEFAULT 0, mode TEXT NOT NULL DEFAULT 'solo', created REAL NOT NULL, updated REAL NOT NULL, started REAL, finished REAL,
    UNIQUE (project_id, seq));
CREATE INDEX IF NOT EXISTS tasks_proj ON tasks (project_id, status);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, ts REAL NOT NULL, who TEXT NOT NULL,
    kind TEXT NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_task ON events (task_id, id);
CREATE TABLE IF NOT EXISTS counters (day TEXT NOT NULL, name TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, name));
"""
_JSON_COLS = {"scope", "deps", "notes", "check_cmd", "runners"}


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in _JSON_COLS & d.keys():
        try:
            d[k] = json.loads(d[k] or "[]")
        except ValueError:
            d[k] = []
    return d


class ProjectDB:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.execute("PRAGMA busy_timeout=15000")
        self._c.executescript(SCHEMA)
        cols = {r[1] for r in self._c.execute("PRAGMA table_info(tasks)")}
        if "mode" not in cols:                                   # databases from 4.0.1 before task modes
            try:
                self._c.execute("ALTER TABLE tasks ADD COLUMN mode TEXT NOT NULL DEFAULT 'solo'")
                self._c.commit()
            except sqlite3.OperationalError as e:                # another process added it a moment ago
                if "duplicate column" not in str(e).lower():
                    raise

    def close(self) -> None:
        with self._lock:
            self._c.close()

    def _q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [_row(r) for r in self._c.execute(sql, args).fetchall()]

    def _x(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            cur = self._c.execute(sql, args)
            self._c.commit()
            return cur.lastrowid or cur.rowcount

    # ------------------------------------------------------------------ projects
    def add_project(self, name: str, path: str, goal: str = "", brief: str = "", check_cmd: list[str] | None = None,
                    runners: list[str] | None = None, base_branch: str = "") -> int:
        now = self.clock()
        return self._x("INSERT INTO projects (name, path, goal, brief, check_cmd, runners, base_branch, created, updated)"
                       " VALUES (?,?,?,?,?,?,?,?,?)",
                       (name, path, goal, brief, json.dumps(check_cmd or []), json.dumps(runners or []),
                        base_branch, now, now))

    def delete_project(self, pid: int) -> None:
        """Only for a project whose opening failed half way (no tasks yet)."""
        self._x("DELETE FROM projects WHERE id=? AND NOT EXISTS (SELECT 1 FROM tasks WHERE project_id=?)", (pid, pid))

    def project(self, pid: int) -> dict | None:
        r = self._q("SELECT * FROM projects WHERE id=?", (pid,))
        return r[0] if r else None

    def projects(self, include_archived: bool = False) -> list[dict]:
        return self._q("SELECT * FROM projects" + ("" if include_archived else " WHERE status!='archived'")
                       + " ORDER BY id")

    def update_project(self, pid: int, **f: Any) -> None:
        if not f:
            return
        f["updated"] = self.clock()
        cols = ", ".join(f"{k}=?" for k in f)
        vals = tuple(json.dumps(v) if k in _JSON_COLS else v for k, v in f.items())
        self._x(f"UPDATE projects SET {cols} WHERE id=?", (*vals, pid))

    # ------------------------------------------------------------------ tasks
    def add_task(self, pid: int, title: str, spec: str = "", acceptance: str = "", scope: list[str] | None = None,
                 deps: list[int] | None = None, status: str = "todo", runner: str = "", model: str = "",
                 mode: str = "solo") -> int:
        now = self.clock()
        with self._lock:
            seq = (self._c.execute("SELECT COALESCE(MAX(seq),0)+1 FROM tasks WHERE project_id=?", (pid,))
                   .fetchone()[0])
            cur = self._c.execute(
                "INSERT INTO tasks (project_id, seq, title, spec, acceptance, scope, deps, status, runner, model,"
                " mode, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, seq, title, spec, acceptance, json.dumps(scope or []), json.dumps(deps or []), status,
                 runner, model, mode, now, now))
            self._c.commit()
            return cur.lastrowid

    def task(self, tid: int) -> dict | None:
        r = self._q("SELECT * FROM tasks WHERE id=?", (tid,))
        return r[0] if r else None

    def tasks(self, pid: int, statuses: tuple[str, ...] | None = None) -> list[dict]:
        if statuses:
            q = ",".join("?" * len(statuses))
            return self._q(f"SELECT * FROM tasks WHERE project_id=? AND status IN ({q}) ORDER BY seq",
                           (pid, *statuses))
        return self._q("SELECT * FROM tasks WHERE project_id=? ORDER BY seq", (pid,))

    def by_seq(self, pid: int, seq: int) -> dict | None:
        r = self._q("SELECT * FROM tasks WHERE project_id=? AND seq=?", (pid, seq))
        return r[0] if r else None

    def update_task(self, tid: int, **f: Any) -> None:
        if not f:
            return
        f["updated"] = self.clock()
        cols = ", ".join(f"{k}=?" for k in f)
        vals = tuple(json.dumps(v) if k in _JSON_COLS else v for k, v in f.items())
        self._x(f"UPDATE tasks SET {cols} WHERE id=?", (*vals, tid))

    def claim(self, tid: int) -> bool:
        """ready -> running, atomically: exactly one dispatcher pass wins a task."""
        now = self.clock()
        with self._lock:
            cur = self._c.execute("UPDATE tasks SET status='running', started=?, updated=?, attempts=attempts+1"
                                  " WHERE id=? AND status='ready'", (now, now, tid))
            self._c.commit()
            return cur.rowcount == 1

    def promote(self, pid: int) -> int:
        """todo -> ready for every task whose dependencies are all done; ready/todo with a failed or cancelled
        dependency -> blocked (reason says which); a task blocked only BY a dependency goes back to todo when that
        dependency is no longer failed/cancelled (retried, split, replaced). Returns how many changed."""
        n = 0
        rows = self.tasks(pid)
        state = {t["id"]: t["status"] for t in rows}
        seq = {t["id"]: t["seq"] for t in rows}
        for t in rows:
            ds = [state.get(d) for d in t["deps"]]
            if t["status"] == "blocked" and t["reason"].startswith(DEP_BLOCK):
                if not any(s in ("failed", "cancelled") for s in ds):
                    self.update_task(t["id"], status="todo", reason="")
                    state[t["id"]] = t["status"] = "todo"
                    n += 1
                else:
                    continue
            if t["status"] not in ("todo", "ready"):
                continue
            if any(s in ("failed", "cancelled") for s in ds):
                bad = next(d for d in t["deps"] if state.get(d) in ("failed", "cancelled"))
                self.update_task(t["id"], status="blocked", reason=f"{DEP_BLOCK}#{seq.get(bad, bad)} {state[bad]}")
                state[t["id"]] = "blocked"
                n += 1
            elif all(s == "done" for s in ds) and t["status"] == "todo":
                self.update_task(t["id"], status="ready")
                state[t["id"]] = "ready"
                n += 1
            elif not all(s == "done" for s in ds) and t["status"] == "ready":
                self.update_task(t["id"], status="todo")
                n += 1
        return n

    # ------------------------------------------------------------------ timeline and counters
    def event(self, tid: int, who: str, kind: str, text: str) -> None:
        with self._lock:
            self._c.execute("INSERT INTO events (task_id, ts, who, kind, text) VALUES (?,?,?,?,?)",
                            (tid, self.clock(), who, kind, text))
            self._c.execute("DELETE FROM events WHERE task_id=? AND id NOT IN "
                            "(SELECT id FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?)", (tid, tid, EVENTS_KEEP))
            self._c.commit()

    def events(self, tid: int, limit: int = 50) -> list[dict]:
        rows = self._q("SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?", (tid, limit))
        return rows[::-1]

    def day(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.clock()))

    def bump(self, name: str, n: int = 1) -> None:
        self._x("INSERT INTO counters (day, name, n) VALUES (?,?,?) ON CONFLICT(day, name) DO UPDATE SET n=n+?",
                (self.day(), name, n, n))

    def counter(self, name: str) -> int:
        r = self._q("SELECT n FROM counters WHERE day=? AND name=?", (self.day(), name))
        return int(r[0]["n"]) if r else 0
