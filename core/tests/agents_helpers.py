"""Shared helpers of the orchestrator tests (SW-V4 E): fake agent executables in temp dirs, a running approvals
service on its own loop, an owner who answers (or not), fake interactive sessions driven over stdin, and
event-driven waits (queues and conditions with deadlines; no sleeps)."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from jarves import approvals as appr
from jarves.config import Config
from jarves.orchestrator import service as svc

FAKES = Path(__file__).parent / "fakes"
WAIT = 60.0


def wait_for(pred, timeout: float = WAIT, what: str = "condition"):
    """Poll a cheap predicate with a deadline (the waited thing is a DB row / process state written by another
    process, so there is no in-process event to block on)."""
    deadline = time.monotonic() + timeout
    ev = threading.Event()
    while True:
        v = pred()
        if v:
            return v
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} not reached in {timeout:.0f} s")
        ev.wait(0.05)


def agents_cfg(**over) -> Config:
    c = Config()
    c.agents.claude_command = [sys.executable, str(FAKES / "fake_claude.py")]
    c.agents.codex_command = [sys.executable, str(FAKES / "fake_codex.py")]
    for k, v in over.items():
        setattr(c.agents, k, v)
    return c


@pytest.fixture
def agent_env(tmp_path, monkeypatch):
    """JARVES_HOME + fake agent state dirs + two allow-listed projects (git repos)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("JARVES_HOME", str(home))
    monkeypatch.setenv("FAKE_CLAUDE_DIR", str(tmp_path / "fake_claude"))
    monkeypatch.setenv("FAKE_CODEX_DIR", str(tmp_path / "fake_codex"))
    monkeypatch.delenv("FAKE_AUTO_REPLY", raising=False)
    monkeypatch.delenv("FAKE_CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("FAKE_CODEX_ACCOUNT", raising=False)
    (tmp_path / "fake_codex").mkdir()
    projects = {}
    for name in ("alpha", "beta", "gamma"):
        p = tmp_path / "work" / name
        p.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(p)], check=True)
        subprocess.run(["git", "-C", str(p), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                        "--allow-empty", "-m", "init"], check=True)
        projects[name] = p
    (tmp_path / "work" / "outside").mkdir()
    return {"home": home, "tmp": tmp_path, "projects": projects, "outside": tmp_path / "work" / "outside"}


class Owner:
    """An approvals service on its own loop thread with one channel that is always 'shown' (like the popup), and
    an owner who answers each request with `policy` ("approve", "deny", "code" = types the shown code, None =
    never answers). Records when each request appeared."""

    name = "popup"

    def __init__(self, home: Path, policy: str | None = "approve", timeout_s: float = 60) -> None:
        self.policy = policy
        self.asked: list[tuple[float, appr.Approval]] = []
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.svc = appr.ApprovalService(home, timeout_s=timeout_s, rate_limit=(100, 60.0))
        self.svc.loop = self.loop
        self.svc.channels.append(self)
        self.cv = threading.Condition()

    def available(self, a) -> bool:
        return True

    def open(self, a) -> None:
        with self.cv:
            self.asked.append((time.monotonic(), a))
            self.cv.notify_all()
        if self.policy is None:
            return
        ok = self.policy in ("approve", "code")
        code = a.typed_code if self.policy == "code" else ""
        self.loop.call_soon(lambda: self.svc.answer(a.id, ok, channel="popup", typed=code))

    def close(self, a) -> None:
        pass

    def stop(self) -> None:
        self.svc.close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


def make_orch(home: Path, cfg: Config, owner: Owner | None = None, notify=None) -> svc.Orchestrator:
    o = svc.Orchestrator(home, cfg, notify=notify)
    if owner is not None:
        o.approvals = owner.svc
    o.start()
    return o


class Host:
    """A fake interactive Claude Code session (tests/fakes/fake_claude_host.py) the owner 'started by hand'."""

    def __init__(self, cwd: Path, plugin_dir: Path, *, session_id: str, channel: bool = False,
                 auto_reply: bool = False, name: str = "") -> None:
        argv = [sys.executable, str(FAKES / "fake_claude_host.py"), "--session-id", session_id, "--plugin-dir",
                str(plugin_dir)]
        if name:
            argv += ["--name", name]
        if channel:
            argv += ["--dangerously-load-development-channels", "plugin:jarves@jarves-local"]
        env = dict(os.environ)
        if auto_reply:
            env["FAKE_AUTO_REPLY"] = "1"
        self.session_id = session_id
        self.proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.seen: list[dict] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                self.events.put(json.loads(line))
            except ValueError:
                pass

    def do(self, **cmd) -> None:
        self.proc.stdin.write(json.dumps(cmd) + "\n")
        self.proc.stdin.flush()

    def expect(self, ev: str, timeout: float = WAIT) -> dict:
        deadline = time.monotonic() + timeout
        for e in self.seen:
            if e.get("ev") == ev:
                self.seen.remove(e)
                return e
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                err = self.proc.stderr.read() if self.proc.poll() is not None else ""
                raise AssertionError(f"host event {ev!r} not seen (got {[e.get('ev') for e in self.seen]}) {err[-800:]}")
            try:
                e = self.events.get(timeout=left)
            except queue.Empty:
                continue
            if e.get("ev") == ev:
                return e
            self.seen.append(e)

    def end(self) -> None:
        if self.proc.poll() is None:
            try:
                self.do(do="end")
                self.proc.wait(60)
            except (OSError, subprocess.TimeoutExpired, ValueError):
                self.proc.kill()
        self.proc.wait(10)


def plugin_dir(home: Path) -> Path:
    from jarves.orchestrator import claude
    return claude.write_plugin(home)
