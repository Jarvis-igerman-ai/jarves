"""Durable work ledger, independent of the Core and any coding-agent process.

This module schedules *already authorised work*, not permissions. A planning
answer never grants a runtime approval. SQLite transactions arbitrate task and
resource claims across the web server, CLI and external supervisor processes.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

STATES = {"queued", "running", "waiting_owner", "blocked", "review", "verified", "done"}
LANES = {"bugs", "features", "release", "ops", "any"}
IMPLEMENTATIONS = {"planned", "in_progress", "merged"}
VALIDATION_RANK = {"not_run": 0, "automated": 1, "app": 2, "device": 3}
DELIVERIES = {"none", "candidate", "beta", "stable"}
OWNER_TTL_S = 600
TOOL_FLIGHT_S = 900
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
ACTIVE_QUESTIONS = {"pending", "deferred"}


def _text(value, name: str, *, maximum: int = 2000, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    value = value.strip()
    if (not value and not empty) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"invalid {name}")
    return value


def _id(value, name: str = "id") -> str:
    value = _text(value, name, maximum=120)
    if not ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")
    return value


def _ids(value, name: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"{name} must be a short list")
    result = [_id(x, name) for x in value]
    if len(result) != len(set(result)):
        raise ValueError(f"duplicate {name}")
    return result


def _choice(value, allowed, name: str):
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"invalid {name}")
    return value


class Store:
    def __init__(self, path: Path | str, clock=time.time, *, timeout_s=15.0):
        if not isinstance(timeout_s, (int, float)) or not 0 <= timeout_s <= 15:
            raise ValueError("invalid workflow database timeout")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), timeout=timeout_s, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(f"PRAGMA busy_timeout={int(timeout_s * 1000)}")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS workflow_records (
                kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY (kind,id));
            CREATE TABLE IF NOT EXISTS workflow_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL NOT NULL,kind TEXT NOT NULL,summary TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_resources (name TEXT PRIMARY KEY,task_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_trace (
                id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL NOT NULL,agent_id TEXT NOT NULL,
                task_id TEXT NOT NULL,parent_id TEXT NOT NULL,event TEXT NOT NULL,tool TEXT NOT NULL,
                call_id TEXT NOT NULL,status TEXT NOT NULL,duration_ms REAL,data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS workflow_trace_agent ON workflow_trace(agent_id,id);
            CREATE INDEX IF NOT EXISTS workflow_trace_task ON workflow_trace(task_id,id);
            CREATE INDEX IF NOT EXISTS workflow_trace_time ON workflow_trace(ts);
        """)
        with self._tx() as db:
            for key, value in {
                "revision": 0,
                "owner": {"mode": "away", "active_until": 0},
                "supervisor": {"heartbeat_at": 0, "status": "offline"},
                "source": {"mode": "empty", "sha": "", "synced_at": 0, "unmapped": []},
                "alerts": [],
                "trace": {"mode": "metadata", "since": self.clock()},
            }.items():
                db.execute("INSERT OR IGNORE INTO workflow_meta VALUES (?,?)", (key, json.dumps(value)))

    @contextmanager
    def _tx(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def close(self):
        with self._lock:
            self._db.close()

    @staticmethod
    def _all(db, kind):
        return [json.loads(r[0]) for r in db.execute(
            "SELECT data FROM workflow_records WHERE kind=? ORDER BY rowid", (kind,))]

    @staticmethod
    def _get(db, kind, ident):
        row = db.execute("SELECT data FROM workflow_records WHERE kind=? AND id=?", (kind, ident)).fetchone()
        if not row:
            raise ValueError(f"unknown {kind}: {ident}")
        return json.loads(row[0])

    @staticmethod
    def _put(db, kind, item):
        db.execute("INSERT INTO workflow_records VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data",
                   (kind, item["id"], json.dumps(item, ensure_ascii=False)))

    @staticmethod
    def _meta(db, key):
        return json.loads(db.execute("SELECT value FROM workflow_meta WHERE key=?", (key,)).fetchone()[0])

    @staticmethod
    def _set_meta(db, key, value):
        db.execute("INSERT OR REPLACE INTO workflow_meta VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def _bump(self, db):
        """Revision-only change: live views refresh, but no event row is written."""
        self._set_meta(db, "revision", self._meta(db, "revision") + 1)

    def _event(self, db, kind, summary):
        db.execute("INSERT INTO workflow_events(ts,kind,summary) VALUES (?,?,?)", (self.clock(), kind, summary[:1000]))
        self._bump(db)
        db.execute("DELETE FROM workflow_events WHERE id < (SELECT COALESCE(MAX(id),0)-2000 FROM workflow_events)")

    @staticmethod
    def _release(db, task, *, locks=True):
        aid = task.get("agent_id")
        if aid:
            agent = Store._get(db, "agent", aid)
            if agent.get("task_id") == task["id"]:
                agent.update(status="idle", task_id=None)
                Store._put(db, "agent", agent)
        if locks:
            db.execute("DELETE FROM workflow_resources WHERE task_id=?", (task["id"],))
        task["agent_id"] = None

    def _quarantine(self, db, task, reason):
        """Retain the writer and locks: an absent acknowledgement is not a stop."""
        task.update(state="blocked", blocker=reason, quarantined=True, updated_at=self.clock())
        if task.get("agent_id"):
            agent = self._get(db, "agent", task["agent_id"])
            agent["status"] = "blocked"
            self._put(db, "agent", agent)
        self._put(db, "task", task)
        self._event(db, "task_quarantined", f"{task['title']}: {reason}; resource locks retained")

    def quarantine(self, task_id, reason):
        """Quarantine an ambiguous dispatch or live writer without releasing locks."""
        reason = _text(reason, "quarantine reason", maximum=2000)
        with self._tx() as db:
            task = self._get(db, "task", _id(task_id))
            if task["state"] not in {"running", "blocked"}:
                raise ValueError("only running or blocked work can be quarantined")
            self._quarantine(db, task, reason)
        return task

    @staticmethod
    def _unanswered(db, task_id):
        return [q for q in Store._all(db, "question") if q["task_id"] == task_id
                and q["blocks"] and q["status"] in ACTIVE_QUESTIONS]

    @staticmethod
    def _ready(db, task):
        if not task.get("execution_allowed", True) or task["state"] != "queued" or Store._unanswered(db, task["id"]):
            return False
        for ident in task["depends_on"]:
            if Store._get(db, "task", ident)["state"] not in {"verified", "done"}:
                return False
        for resource in task["resources"]:
            if db.execute("SELECT 1 FROM workflow_resources WHERE name=?", (resource,)).fetchone():
                return False
        return True

    def snapshot(self):
        with self._tx() as db:
            now = self.clock()
            owner = self._meta(db, "owner")
            owner["effective_mode"] = "present" if owner["mode"] == "present" and owner["active_until"] > now else "away"
            supervisor = self._meta(db, "supervisor")
            if now - supervisor["heartbeat_at"] > 120:
                supervisor["status"] = "offline"
            tasks = self._all(db, "task")
            for task in tasks:
                task["ready"] = self._ready(db, task)
            return {
                "revision": self._meta(db, "revision"), "observed_at": now,
                "owner": owner, "supervisor": supervisor,
                "tasks": tasks, "agents": self._all(db, "agent"),
                "questions": self._all(db, "question"), "alerts": self._meta(db, "alerts"),
                "events": [dict(r) for r in db.execute("SELECT * FROM workflow_events ORDER BY id DESC LIMIT 100")],
                "source": self._meta(db, "source"),
            }

    def create_task(self, data):
        if not isinstance(data, dict):
            raise ValueError("task must be an object")
        if len(json.dumps(data, ensure_ascii=False)) > 64000:
            raise ValueError("task data too large")
        ident = _id(data.get("id", "task-" + uuid.uuid4().hex[:12]))
        title = _text(data.get("title"), "title", maximum=500)
        lane = _choice(data.get("lane", "features"), LANES - {"any"}, "lane")
        priority = data.get("priority", 2)
        if type(priority) is not int or not 0 <= priority <= 3:
            raise ValueError("priority must be 0..3")
        acceptance = []
        supplied = data.get("acceptance", [])
        if not isinstance(supplied, list) or len(supplied) > 100:
            raise ValueError("invalid acceptance")
        for index, row in enumerate(supplied):
            if not isinstance(row, dict):
                raise ValueError("invalid acceptance row")
            acceptance.append({"id": _id(row.get("id", f"C{index + 1}")),
                               "label": _text(row.get("label"), "acceptance label", maximum=1000),
                               "passed": False, "evidence": ""})
        if len({a["id"] for a in acceptance}) != len(acceptance):
            raise ValueError("duplicate acceptance id")
        task = {"id": ident, "title": title,
                "version": _text(data.get("version", "V5"), "version", maximum=32),
                "lane": lane, "priority": priority, "state": "queued", "agent_id": None,
                "depends_on": _ids(data.get("depends_on", []), "depends_on"),
                "resources": _ids(data.get("resources", []), "resources"),
                "implementation": "planned", "validation": "not_run",
                "required_validation": _choice(data.get("required_validation", "app"), VALIDATION_RANK.keys() - {"not_run"}, "required_validation"),
                "delivery": "none", "acceptance": acceptance, "blocker": "", "updated_at": self.clock(),
                "execution_allowed": data.get("execution_allowed", True), "code_revision": "", "artifact_ref": ""}
        if type(task["execution_allowed"]) is not bool:
            raise ValueError("execution_allowed must be boolean")
        for name in ("issue_numbers", "source_paths", "source_url", "source_urls", "scope_note", "requirement_id", "owner_gate", "scope"):
            if name in data:
                # These are descriptive imported metadata, never executable instructions.
                value = json.loads(json.dumps(data[name]))
                if len(json.dumps(value)) > 16000:
                    raise ValueError(f"{name} too long")
                task[name] = value
        with self._tx() as db:
            if db.execute("SELECT 1 FROM workflow_records WHERE kind='task' AND id=?", (ident,)).fetchone():
                raise ValueError("task already exists")
            for dependency in task["depends_on"]:
                self._get(db, "task", dependency)
            self._put(db, "task", task)
            self._event(db, "task_created", title)
        return task

    def register_agent(self, data):
        if not isinstance(data, dict):
            raise ValueError("agent must be an object")
        ident = _id(data.get("id"))
        now = self.clock()
        with self._tx() as db:
            old = db.execute("SELECT data FROM workflow_records WHERE kind='agent' AND id=?", (ident,)).fetchone()
            agent = json.loads(old[0]) if old else {"id": ident, "status": "idle", "task_id": None,
                                                       "progress_at": now, "note": "", "auto_dispatch": False}
            auto_dispatch = data.get("auto_dispatch", agent.get("auto_dispatch", False))
            if type(auto_dispatch) is not bool:
                raise ValueError("auto_dispatch must be boolean")
            agent.update(label=_text(data.get("label", ident), "label", maximum=200),
                         lane=_choice(data.get("lane", "any"), LANES, "lane"), heartbeat_at=now,
                         auto_dispatch=auto_dispatch)
            for key in ("session_id", "provider", "project", "parent_id", "requested_model"):
                if key in data:
                    agent[key] = _text(data[key], key, maximum=200, empty=True)
            if old and json.loads(old[0]).get("task_id") and agent.get("session_id") != json.loads(old[0]).get("session_id"):
                raise ValueError("cannot replace a session while it owns a task")
            if agent.get("session_id") and any(other["id"] != ident and other.get("session_id") == agent["session_id"]
                                               for other in self._all(db, "agent")):
                raise ValueError("session already attached to another workflow worker")
            self._put(db, "agent", agent)
            self._event(db, "agent_registered", f"Registered {agent['label']}")
        return agent

    def observe_agent(self, agent_id, status, note="", tool=None):
        """Native lifecycle telemetry; it does not claim work or attest completion.

        tool="start" marks a tool call in flight (a long Bash call emits no hooks);
        tool="end" clears it.
        """
        _choice(status, {"idle", "running", "blocked", "offline"}, "observed status")
        note = _text(note, "note", maximum=1000, empty=True)
        with self._tx() as db:
            agent = self._get(db, "agent", _id(agent_id))
            if agent.get("task_id"):
                # Task ownership changes only through the ledger's checkpoint/evidence paths.
                agent["note"] = note
                agent["heartbeat_at"] = self.clock()
                # Live activity of a task owner whose task is still running revives a stale flag.
                if agent["status"] in {"offline", "blocked"} and self._get(db, "task", agent["task_id"])["state"] == "running":
                    agent["status"] = "running"
            else:
                agent.update(status=status, note=note, heartbeat_at=self.clock(), progress_at=self.clock())
            if tool == "start":
                agent["tool_started_at"] = self.clock()
            elif tool == "end":
                agent.pop("tool_started_at", None)
            self._put(db, "agent", agent)
            self._bump(db)
        return agent

    def manager_continue(self, agent_id):
        """At most two native Stop continuations/hour, only for truly ready work.

        This is a bounded 'do not stop with authorised work' signal, never a
        permission grant or an attempt to resurrect a dead Claude process.
        """
        with self._tx() as db:
            ready = [t["id"] for t in self._all(db, "task") if self._ready(db, t)]
            if not ready:
                return []
            key = "native_continue:" + _id(agent_id)
            row = db.execute("SELECT value FROM workflow_meta WHERE key=?", (key,)).fetchone()
            times = [ts for ts in (json.loads(row[0]) if row else []) if self.clock() - ts < 3600]
            if len(times) >= 2:
                return []
            times.append(self.clock())
            self._set_meta(db, key, times)
            self._event(db, "manager_continue", "Native manager has authorised ready work; one bounded continuation requested")
            return ready[:5]

    def heartbeat(self, agent_id, note="", progress=False, observed_at=None):
        note = _text(note, "note", maximum=1000, empty=True)
        observed_at = self.clock() if observed_at is None else observed_at
        if type(observed_at) not in (int, float) or not math.isfinite(observed_at):
            raise ValueError("invalid observed_at")
        observed_at = min(observed_at, self.clock())
        with self._tx() as db:
            agent = self._get(db, "agent", _id(agent_id))
            agent.update(heartbeat_at=max(agent["heartbeat_at"], observed_at), note=note)
            if progress:
                agent["progress_at"] = max(agent["progress_at"], observed_at)
            # A recovered process must explicitly checkpoint/recover its quarantined task.
            if agent["status"] == "offline" and not agent.get("task_id"):
                agent["status"] = "idle"
            self._put(db, "agent", agent)
            self._bump(db)
        return agent

    def claim(self, agent_id, task_id=None):
        agent_id = _id(agent_id)
        with self._tx() as db:
            agent = self._get(db, "agent", agent_id)
            native_request = agent.get("provider") == "claude-native" and agent["status"] == "running"
            if agent.get("task_id") or (agent["status"] != "idle" and not native_request):
                return None
            if self.clock() - agent["heartbeat_at"] > 120:
                return None
            tasks = sorted(self._all(db, "task"), key=lambda t: (t["priority"], t["updated_at"], t["id"]))
            for task in tasks:
                if task_id and task["id"] != task_id:
                    continue
                if not task.get("execution_allowed", True) or agent["lane"] not in {"any", task["lane"]} or not self._ready(db, task):
                    continue
                for name in task["resources"]:
                    db.execute("INSERT INTO workflow_resources VALUES (?,?)", (name, task["id"]))
                task.update(state="running", agent_id=agent_id, blocker="", updated_at=self.clock())
                if task["implementation"] == "planned":
                    task["implementation"] = "in_progress"
                agent.update(status="running", task_id=task["id"], heartbeat_at=self.clock(), progress_at=self.clock())
                self._put(db, "task", task)
                self._put(db, "agent", agent)
                self._event(db, "task_claimed", f"{agent['label']}: {task['title']}")
                return task
        return None

    def ask(self, data):
        if not isinstance(data, dict):
            raise ValueError("question must be an object")
        if len(json.dumps(data, ensure_ascii=False)) > 24000:
            raise ValueError("question data too large")
        title = _text(data.get("title"), "question title", maximum=500)
        ident = _id(data.get("id", "q-" + uuid.uuid4().hex[:12]))
        choices = data.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 8:
            raise ValueError("invalid choices")
        choices = [{"id": _id(c.get("id")), "label": _text(c.get("label"), "choice", maximum=300)} for c in choices]
        if len({c["id"] for c in choices}) != len(choices):
            raise ValueError("duplicate choice")
        if type(data.get("blocks", True)) is not bool:
            raise ValueError("blocks must be boolean")
        with self._tx() as db:
            task = self._get(db, "task", _id(data.get("task_id")))
            if task.get("quarantined"):
                raise ValueError("quarantined task requires explicit recovery before asking")
            if task["state"] in {"verified", "done"}:
                raise ValueError("completed task cannot wait for a question")
            if db.execute("SELECT 1 FROM workflow_records WHERE kind='question' AND id=?", (ident,)).fetchone():
                raise ValueError("question already exists")
            blocks = data.get("blocks", True)
            checkpoint = _text(data.get("checkpoint", ""), "checkpoint", maximum=2000, empty=True)
            if blocks and (task["state"] == "running" or self._has_locks(db, task["id"])) and not checkpoint:
                raise ValueError("checkpoint required before releasing a running task")
            question = {"id": ident, "task_id": task["id"], "title": title,
                        "context": _text(data.get("context", ""), "context", maximum=4000, empty=True),
                        "choices": choices, "blocks": blocks, "status": "pending", "answer": None,
                        "created_at": self.clock(), "checkpoint": checkpoint}
            if blocks:
                self._release(db, task)
                task.update(state="waiting_owner", blocker=title, updated_at=self.clock())
                self._put(db, "task", task)
            self._put(db, "question", question)
            self._event(db, "question_asked", title)
        return question

    @staticmethod
    def _has_locks(db, task_id):
        return bool(db.execute("SELECT 1 FROM workflow_resources WHERE task_id=?", (task_id,)).fetchone())

    def answer(self, question_id, answer):
        answer = _text(answer, "answer", maximum=4000)
        with self._tx() as db:
            question = self._get(db, "question", _id(question_id))
            if question["status"] not in ACTIVE_QUESTIONS:
                raise ValueError("question already answered")
            question.update(status="answered", answer=answer, answered_at=self.clock())
            self._put(db, "question", question)
            task = self._get(db, "task", question["task_id"])
            if task["state"] == "waiting_owner" and not self._unanswered(db, task["id"]):
                task.update(state="queued", blocker="", updated_at=self.clock())
                self._put(db, "task", task)
            self._event(db, "question_answered", question["title"])
        return question

    def defer(self, question_id):
        with self._tx() as db:
            question = self._get(db, "question", _id(question_id))
            if question["status"] not in ACTIVE_QUESTIONS:
                raise ValueError("question already answered")
            question.update(status="deferred", deferred_at=self.clock())
            self._put(db, "question", question)
            self._event(db, "question_deferred", question["title"])
        return question

    def set_owner(self, mode):
        _choice(mode, {"present", "away"}, "owner mode")
        owner = {"mode": mode, "active_until": self.clock() + OWNER_TTL_S if mode == "present" else 0}
        with self._tx() as db:
            self._set_meta(db, "owner", owner)
            self._event(db, "owner_presence", f"Owner {mode}")
        return owner

    @staticmethod
    def _proof(task):
        checks = task["acceptance"]
        if not checks or not all(c["passed"] and c["evidence"].strip() for c in checks):
            raise ValueError("all acceptance criteria need passing evidence")
        if not task.get("code_revision", "").strip():
            raise ValueError("verification needs an explicit code revision")
        if VALIDATION_RANK[task["validation"]] < VALIDATION_RANK[task["required_validation"]]:
            raise ValueError("required validation has not been performed")
        if task["implementation"] != "merged":
            raise ValueError("implementation must be merged before verification")
        if any(not c.get("code_revision") or c["code_revision"] != task["code_revision"] for c in checks):
            raise ValueError("acceptance evidence belongs to another code revision")

    def transition(self, task_id, data):
        if not isinstance(data, dict):
            raise ValueError("transition must be an object")
        with self._tx() as db:
            task = self._get(db, "task", _id(task_id))
            state = _choice(data.get("state", task["state"]), STATES, "state")
            if "execution_allowed" in data:
                if type(data["execution_allowed"]) is not bool:
                    raise ValueError("execution_allowed must be boolean")
                task["execution_allowed"] = data["execution_allowed"]
            if state == "running" and task["state"] != "running":
                raise ValueError("use claim to start work")
            if task["state"] == "done" and state not in {"done", "queued", "blocked"}:
                raise ValueError("reopen a done task before changing its state")
            if self._unanswered(db, task["id"]) and state not in {"waiting_owner", "blocked"}:
                raise ValueError("task has unanswered owner questions")
            held = self._has_locks(db, task["id"])
            if task.get("quarantined") and state != "blocked":
                if data.get("recovery_confirmed") is not True:
                    raise ValueError("quarantined task requires explicit recovery confirmation")
                _text(data.get("checkpoint"), "recovery checkpoint", maximum=2000)
                self._release(db, task)
                task.pop("quarantined", None)
            if task["state"] == "running" and state in {"queued", "waiting_owner", "blocked"} and held:
                _text(data.get("checkpoint"), "checkpoint", maximum=2000)
            if "code_revision" in data:
                revision = _text(data["code_revision"], "code_revision", maximum=200, empty=True)
                if revision != task.get("code_revision", ""):
                    task["code_revision"] = revision
                    task["validation"] = "not_run"
                    task["delivery"] = "none"
                    task["artifact_ref"] = ""
                    for row in task["acceptance"]:
                        row["passed"] = False
                    if state in {"verified", "done"}:
                        state = "review"
            for key, allowed in (("implementation", IMPLEMENTATIONS), ("validation", VALIDATION_RANK), ("delivery", DELIVERIES)):
                if key in data:
                    task[key] = _choice(data[key], allowed, key)
            if "artifact_ref" in data:
                task["artifact_ref"] = _text(data["artifact_ref"], "artifact_ref", maximum=2000, empty=True)
            if state in {"verified", "done"}:
                self._proof(task)
            if state == "done" and task["delivery"] not in {"beta", "stable"}:
                raise ValueError("done needs an identified beta or stable delivery")
            if state == "done" and not task["artifact_ref"]:
                raise ValueError("done needs an artifact_ref identifying the tested delivery")
            if task["state"] == "running" and state != "running":
                self._release(db, task)
            if state in {"queued", "blocked"} and task["state"] in {"verified", "done"}:
                # Reopening must invalidate verification; old evidence remains for diagnosis only.
                task["validation"] = "not_run"
                for row in task["acceptance"]:
                    row["passed"] = False
            task.update(state=state, updated_at=self.clock())
            if "blocker" in data:
                task["blocker"] = _text(data["blocker"], "blocker", maximum=2000, empty=True)
            elif state != "blocked":
                task["blocker"] = ""
            self._put(db, "task", task)
            self._event(db, "task_transition", f"{task['title']}: {state}")
        return task

    def record_check(self, task_id, criterion_id, evidence, passed=True):
        if type(passed) is not bool:
            raise ValueError("passed must be boolean")
        evidence = _text(evidence, "evidence", maximum=4000, empty=not passed)
        with self._tx() as db:
            task = self._get(db, "task", _id(task_id))
            matches = [c for c in task["acceptance"] if c["id"] == _id(criterion_id)]
            if not matches:
                raise ValueError("unknown acceptance criterion")
            matches[0].update(passed=passed, evidence=evidence, checked_at=self.clock(), code_revision=task.get("code_revision", ""))
            task["updated_at"] = self.clock()
            if not passed and task["state"] in {"verified", "done"}:
                task.update(state="review", blocker="Acceptance regression")
            self._put(db, "task", task)
            self._event(db, "acceptance_recorded", f"{task['title']}: {'pass' if passed else 'fail'}")
        return task

    def tick(self, stale_s=120, stuck_s=1800):
        if stale_s <= 0 or stuck_s <= 0:
            raise ValueError("watchdog thresholds must be positive")
        with self._tx() as db:
            now = self.clock()
            alerts = []
            changed = False
            # Only the actual external supervisor writes its heartbeat. A web
            # refresh/dispatch check must not impersonate a running watchdog.
            for agent in self._all(db, "agent"):
                # A tool call in flight (long Bash/tests) emits no hook events; allow up to 15 min.
                in_flight = now - agent.get("tool_started_at", -TOOL_FLIGHT_S) < TOOL_FLIGHT_S
                stale = now - agent["heartbeat_at"] > stale_s and not in_flight
                stuck = bool(agent.get("task_id")) and now - agent["progress_at"] > stuck_s
                if stale and not agent.get("task_id") and not stuck:
                    # A finished agent/ended session is simply offline; nothing to alert about.
                    if agent["status"] != "offline":
                        agent["status"] = "offline"
                        self._put(db, "agent", agent)
                        changed = True
                    continue
                if stale or stuck:
                    reason = "worker_offline" if stale else "worker_stalled"
                    alerts.append({"kind": reason, "summary": f"{agent['label']}: {'heartbeat expired' if stale else 'no progress'}",
                                   "agent_id": agent["id"], "task_id": agent.get("task_id")})
                    aid = agent.get("task_id")
                    if aid:
                        task = self._get(db, "task", aid)
                        if task["state"] == "running":
                            self._quarantine(db, task, reason)
                    agent["status"] = "offline" if stale else "blocked"
                    self._put(db, "agent", agent)
                    continue
                if agent["status"] == "idle":
                    eligible = [t for t in self._all(db, "task") if agent["lane"] in {"any", t["lane"]} and self._ready(db, t)]
                    if eligible:
                        alerts.append({"kind": "idle_with_work", "summary": f"{agent['label']}: {len(eligible)} eligible tasks",
                                       "agent_id": agent["id"], "task_id": None})
            tasks = self._all(db, "task")
            for task in tasks:
                if task.get("execution_allowed", True) and task["state"] == "queued" and not self._ready(db, task):
                    alerts.append({"kind": "dependency_wait", "summary": f"{task['title']}: dependency or resource unavailable",
                                   "task_id": task["id"], "agent_id": None})
            executable = [t for t in tasks if t.get("execution_allowed", True)]
            if not any(t["state"] in {"running", "queued"} for t in executable) and any(t["state"] == "waiting_owner" for t in executable):
                alerts.append({"kind": "all_work_waiting_owner", "summary": "Remaining work needs owner decisions; no work was invented",
                               "task_id": None, "agent_id": None})
            if not self._all(db, "agent") and any(self._ready(db, t) for t in tasks):
                alerts.append({"kind": "no_workers", "summary": "Ready work exists but no workers are registered",
                               "task_id": None, "agent_id": None})
            if alerts != self._meta(db, "alerts"):
                self._set_meta(db, "alerts", alerts)
                self._event(db, "supervisor_tick", f"Supervisor checked work: {len(alerts)} alerts")
            elif changed:
                self._bump(db)
        return alerts

    def set_source(self, data):
        if not isinstance(data, dict):
            raise ValueError("source must be an object")
        if len(json.dumps(data)) > 64000:
            raise ValueError("source metadata too large")
        with self._tx() as db:
            source = {**self._meta(db, "source"), **data}
            self._set_meta(db, "source", source)
            self._event(db, "source_updated", "Source inventory updated")
        return source

    def update_metadata(self, task_id, changes, *, authorize_execution=False):
        """Attach descriptive source data without upgrading implementation or proof.

        Sync callers use the default. Only an explicit operator path may opt into
        changing execution authorisation; GitHub labels/closed issues cannot do it.
        """
        allowed = {"source_issues", "source_prs", "source_paths", "mapping_note", "source_closed",
                   "source_updated_at", "source_synced_at", "owner_gate", "scope", "execution_allowed"}
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise ValueError("unsupported task metadata")
        if len(json.dumps(changes, ensure_ascii=False)) > 48000:
            raise ValueError("task metadata too large")
        if "execution_allowed" in changes:
            if not authorize_execution:
                raise ValueError("execution authorisation requires an explicit operator action")
            if type(changes["execution_allowed"]) is not bool:
                raise ValueError("execution_allowed must be boolean")
        if "source_closed" in changes and type(changes["source_closed"]) is not bool:
            raise ValueError("source_closed must be boolean")
        for key in ("source_issues", "source_prs", "source_paths"):
            if key in changes and (not isinstance(changes[key], list) or len(changes[key]) > 200):
                raise ValueError(f"invalid {key}")
        if "mapping_note" in changes:
            _text(changes["mapping_note"], "mapping_note", maximum=4000, empty=True)
        with self._tx() as db:
            task = self._get(db, "task", _id(task_id))
            task.update(json.loads(json.dumps(changes)))
            task["updated_at"] = self.clock()
            self._put(db, "task", task)
            self._event(db, "task_metadata", f"Source metadata updated: {task['title']}")
        return task

    def set_supervisor(self, data):
        if not isinstance(data, dict):
            raise ValueError("supervisor must be an object")
        status = _text(data.get("status", "watching"), "supervisor status", maximum=80)
        heartbeat_at = data.get("heartbeat_at", self.clock())
        if type(heartbeat_at) not in (int, float) or not math.isfinite(heartbeat_at):
            raise ValueError("invalid supervisor heartbeat")
        with self._tx() as db:
            previous = self._meta(db, "supervisor")
            supervisor = {**previous, **data,
                          "heartbeat_at": min(heartbeat_at, self.clock()), "status": status}
            if len(json.dumps(supervisor)) > 16000:
                raise ValueError("supervisor metadata too large")
            self._set_meta(db, "supervisor", supervisor)
            if previous.get("status") != status:
                self._event(db, "supervisor_status", f"Supervisor {status}")
            else:
                self._bump(db)
        return supervisor

    def record_event(self, kind, summary):
        kind = _id(kind, "event kind")
        summary = _text(summary, "event summary", maximum=1000)
        with self._tx() as db:
            self._event(db, kind, summary)
        return {"kind": kind, "summary": summary}

    def seed_plan(self, plan):
        """Import scope only; reruns never replace actual progress or evidence."""
        if not isinstance(plan, dict):
            raise ValueError("plan must be an object")
        requirements = {}
        for version in plan.get("versions", []):
            for requirement in version.get("requirements", []):
                ident = _id(requirement.get("id"))
                if ident in requirements:
                    raise ValueError("duplicate requirement id")
                requirements[ident] = {**requirement, "version": version["id"], "execution_allowed": False}
        if len(requirements) > 2000:
            raise ValueError("plan too large")
        with self._tx() as db:
            existing = {t["id"] for t in self._all(db, "task")}
        # Validate and order the whole graph before importing anything. Version
        # source order is not an execution order: V7 tracks cross-reference M1.
        ordered, visiting, visited = [], set(), set()

        def visit(ident):
            if ident in visited or ident in existing:
                return
            if ident in visiting:
                raise ValueError("cyclic requirement dependencies")
            if ident not in requirements:
                raise ValueError(f"unknown requirement dependency: {ident}")
            visiting.add(ident)
            for dependency in _ids(requirements[ident].get("depends_on", []), "depends_on"):
                visit(dependency)
            visiting.remove(ident)
            visited.add(ident)
            ordered.append(requirements[ident])

        for ident in requirements:
            visit(ident)
        created = []
        for data in ordered:
            try:
                created.append(self.create_task(data))
            except ValueError as exc:
                # Another process may have imported this same stable scope ID.
                if str(exc) != "task already exists":
                    raise
        unmapped = [ident for ident, row in requirements.items() if not row.get("issue_numbers")]
        with self._tx() as db:
            source = self._meta(db, "source")
            if source.get("mode") in {"empty", "scope"}:
                source.update(mode="scope", sha=plan.get("baseline_sha", ""), synced_at=self.clock(),
                              scope_note=plan.get("scope_note", ""), unmapped=unmapped)
            else:
                # A repeat scope import must not disguise a failed or stale
                # GitHub fetch as a successful fresh source synchronisation.
                source["unmapped"] = list(dict.fromkeys([*source.get("unmapped", []), *unmapped]))
            source.update(baseline_sha=plan.get("baseline_sha", ""), scope_unmapped=unmapped)
            self._set_meta(db, "source", source)
            self._event(db, "scope_imported", f"Scope imported: {len(created)} new requirements; {len(unmapped)} unmapped")
        return {"created": len(created), "tasks": created}
