"""SW-V4 E (#49) system tests: a real Core with the switch on, the real named pipe, fake Claude Code / Codex processes
speaking the recorded protocols, driven by the voice tools (registry), the CLI in ANOTHER process (over the bridge)
and the dashboard HTTP API. Also: the switch hides everything, crash / restart recovery (UC6: an orchestrator
process killed hard, queued messages delivered exactly once, last-known states), planted secrets never reach the
logs / agents.db / approvals audit, and the idle cost of the bridge + registry."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from agents_helpers import FAKES, Host, agent_env, agents_cfg, plugin_dir, wait_for  # noqa: F401
from conftest import FakeSTT, FakeTTS, free_port
from jarves.orchestrator import FEATURE
from jarves.orchestrator import service as svc
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio

SRC = str(Path(__file__).resolve().parents[1] / "src")


class Answerer:
    """The owner at the approvals popup of the real Core (approve / deny / type the code)."""
    name = "popup"

    def __init__(self, svc_, policy="approve"):
        self.svc, self.policy, self.asked = svc_, policy, []

    def available(self, a):
        return True

    def open(self, a):
        """Like the real popup: the owner's click arrives from the popup's watcher thread (never from inside the
        tool that asked - approvals.answer refuses that)."""
        import threading
        self.asked.append(a)
        if self.policy:
            ok = self.policy in ("approve", "code")
            typed = a.typed_code if self.policy == "code" else ""
            threading.Thread(target=lambda: self.svc.answer(a.id, ok, channel="popup", typed=typed),
                             daemon=True).start()

    def close(self, a):
        pass


def _cfg_file(home: Path, cfg) -> None:
    (home / "config.toml").write_text(
        "[agents]\nclaude_command = " + json.dumps(cfg.agents.claude_command) + "\ncodex_command = "
        + json.dumps(cfg.agents.codex_command) + "\n", encoding="utf-8")


@pytest.fixture
async def acore(agent_env):
    from jarves.app import Core
    home = agent_env["home"]
    cfg = agents_cfg(permission_wait_s=20)
    cfg.link.port = free_port()
    cfg.voice.greeting = False
    cfg.voice.greeting_delay_s = 0
    _cfg_file(home, cfg)
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    ans = Answerer(c.approvals)
    c.approvals.channels.append(ans)
    c.features.set(FEATURE, True)
    o = wait_for(lambda: c.agents_orchestrator["o"], what="orchestrator started by the switch")
    o.projects.add(str(agent_env["projects"]["alpha"]), "alpha", ["אלפא"])
    o.projects.add(str(agent_env["projects"]["beta"]), "beta")
    yield {"core": c, "o": o, "ans": ans, "env": agent_env, "home": home, "cfg": cfg}
    if c.health.core != "stopped":
        await c.stop()


def cli(home: Path, *args, timeout=120) -> tuple[int, str]:
    env = dict(os.environ, JARVES_HOME=str(home), PYTHONPATH=SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))
    p = subprocess.run([sys.executable, "-m", "jarves", "agents", *args], capture_output=True, text=True,
                       encoding="utf-8", env=env, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


async def _in_thread(fn, *a, **kw):
    import asyncio
    return await asyncio.to_thread(fn, *a, **kw)


async def test_voice_tools_cli_and_dashboard_see_the_same_sessions(acore):
    import asyncio
    core, o, env, home = acore["core"], acore["o"], acore["env"], acore["home"]
    h = await asyncio.to_thread(Host, env["projects"]["alpha"], plugin_dir(home), session_id=str(uuid.uuid4()),
                                auto_reply=True)
    try:
        await asyncio.to_thread(h.expect, "session_start")
        sid = f"claude:{h.session_id}"
        await asyncio.to_thread(wait_for, lambda: (o.db.session(sid) or {}).get("plugin"))
        names = {t.name for t in core.registry.tools()}
        assert {"agents_list", "agents_status", "agents_ask", "agents_tell", "agents_start", "agents_stop",
                "agents_digest"} <= names
        r = await core.registry.call("agents_status", {"session": "אלפא"})
        assert r.ok and r.message.startswith("קלוד בפרויקט alpha") and r.data["source"] == "snapshot"
        # the agent's text in a result is untrusted: no model-origin action may follow it in the same turn
        bad = await core.registry.call("agents_tell", {"session": "alpha", "action": "continue"})
        assert not bad.ok and bad.code == "TOOL_UNTRUSTED_TURN"
        core.registry.new_turn()
        core.registry._untrusted_at = None                              # the owner spoke again
        r = await core.registry.call("agents_ask", {"session": "claude alpha", "question": "מה חוסם אותך?"})
        assert r.ok and r.code == "QUEUED", r
        # the CLI in another process reaches the Core over the named pipe
        rc, out = await asyncio.to_thread(cli, home, "list", "--json")
        assert rc == 0, out
        d = json.loads(out)
        assert [s["id"] for s in d["sessions"]] == [sid]
        rc, out = await asyncio.to_thread(cli, home, "status", "alpha")
        assert rc == 0 and "Claude in alpha" in out
        rc, out = await asyncio.to_thread(cli, home, "tell", sid, "please run the targeted tests")
        assert rc == 0 and "Queued" in out
        # the dashboard API (in-Core dashboard)
        from jarves import dashboard
        from test_dashboard import get, req
        dsh = dashboard.Dashboard(home, acore["cfg"]).start()
        try:
            d = await asyncio.to_thread(get, dsh, "/api/agents?digest=1&lang=he")
            assert d["enabled"] and d["sessions"][0]["id"] == sid and "digest" in d
            r, body = await asyncio.to_thread(req, dsh, "POST", "/api/agents/tell", headers={"X-Jarves": "1"},
                                              body={"ref": sid, "text": "and then commit"})
            assert r.status == 200 and json.loads(body)["code"] == "QUEUED"
            s = await asyncio.to_thread(get, dsh, f"/api/agents/session/{sid}")
            assert s["ok"] and s["session"]["provider"] == "claude"
            assert (await asyncio.to_thread(get, dsh, "/api/agents/projects"))["projects"][0]["name"] == "alpha"
        finally:
            await asyncio.to_thread(dsh.stop)
        # all three queued messages are delivered together, once, at the session's next stop
        await asyncio.to_thread(h.do, do="stop", text="checkpoint")
        stop = await asyncio.to_thread(h.expect, "stop")
        reason = stop["output"]["reason"]
        assert reason.count("The owner") == 3 and "מה חוסם אותך?" in reason
    finally:
        await asyncio.to_thread(h.end)


async def test_start_by_voice_needs_the_screen_and_switch_off_hides_all(acore):
    import asyncio
    core, ans, home = acore["core"], acore["ans"], acore["home"]
    ans.policy = "deny"
    r = await core.registry.call("agents_start", {"provider": "codex", "project": "beta", "task": "add a test"})
    assert not r.ok and r.code == "NOT_APPROVED"
    a = ans.asked[-1]
    assert a.voice_ok is False and a.title == "agents_start"            # a spoken "yes" cannot start it
    ans.policy = "approve"
    r = await core.registry.call("agents_start", {"provider": "codex", "project": "beta", "task": "add a test"})
    assert r.ok and r.data["session"]["origin"] == "managed"
    core.features.set(FEATURE, False)
    assert not ({"agents_list", "agents_status"} & {t.name for t in core.registry.tools()})
    off = await core.registry.call("agents_list", {})
    assert not off.ok and off.code == "FEATURE_OFF"
    assert core.agents_orchestrator["o"] is None                        # the bridge is down with the switch
    rc, out = await asyncio.to_thread(cli, home, "list")
    assert rc == 2 and "jarvis features on agent_orchestrator" in out


async def test_planted_secrets_never_reach_logs_db_or_audit(acore):
    import asyncio
    o, env, home = acore["o"], acore["env"], acore["home"]
    secrets_ = ["sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX1234", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                "AKIAABCDEFGHIJKLMNOP", "xoxb-1234567890-abcdefghij"]
    h = await asyncio.to_thread(Host, env["projects"]["alpha"], plugin_dir(home), session_id=str(uuid.uuid4()))
    try:
        await asyncio.to_thread(h.expect, "session_start")
        sid = f"claude:{h.session_id}"
        await asyncio.to_thread(wait_for, lambda: (o.db.session(sid) or {}).get("plugin"))
        await asyncio.to_thread(h.do, do="report", args={"summary": "deployed with " + " ".join(secrets_),
                                                         "blocker": "token=" + secrets_[0]})
        await asyncio.to_thread(h.expect, "reported")
        await asyncio.to_thread(h.do, do="permission", command=f"curl -H 'Authorization: Bearer {secrets_[1]}' x")
        await asyncio.to_thread(h.expect, "permission")
        await asyncio.to_thread(h.do, do="stop", text="done; key " + secrets_[2])
        await asyncio.to_thread(h.expect, "stop")
    finally:
        await asyncio.to_thread(h.end)
    blob = b""
    for p in home.rglob("*"):
        if p.is_file() and p.suffix not in (".token",):
            try:
                blob += p.read_bytes()
            except OSError:
                pass
    for s in secrets_:
        assert s.encode() not in blob, s
    tok = (home / "agents" / "bridge.token").read_text(encoding="ascii")
    logs_blob = b"".join(p.read_bytes() for p in home.rglob("*.log*") if p.is_file()) + \
        b"".join(p.read_bytes() for p in home.rglob("*.jsonl") if p.is_file())
    assert tok.encode() not in logs_blob                                # the pipe token is never logged


def test_crash_and_restart_delivers_queued_messages_exactly_once(agent_env):
    """UC6: the orchestrator process is killed hard while a message waits; the session's hooks keep running (spooled
    events, delivery straight from agents.db); a new orchestrator shows last-known state and never repeats it."""
    home = agent_env["home"]
    cfg = agents_cfg()
    env = dict(os.environ, PYTHONPATH=SRC)
    o_proc = subprocess.Popen([sys.executable, str(FAKES / "orch_proc.py"), str(home),
                               json.dumps(cfg.agents.claude_command), json.dumps(cfg.agents.codex_command)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    assert json.loads(o_proc.stdout.readline())["ready"]
    from jarves.orchestrator import policy
    policy.Projects(home).add(str(agent_env["projects"]["alpha"]), "alpha")
    h = Host(agent_env["projects"]["alpha"], plugin_dir(home), session_id=str(uuid.uuid4()))
    try:
        h.expect("session_start")
        sid = f"claude:{h.session_id}"
        r = wait_for(lambda: (svc.call(home, cfg, "status", ref=sid) if
                              svc.call(home, cfg, "status", ref=sid).get("ok") else None), what="registered")
        assert r["session"]["plugin"]
        q = svc.call(home, cfg, "tell", ref=sid, text="please run the tests")   # over the bridge
        assert q["code"] == "QUEUED"
        o_proc.kill()                                                           # hard crash, no cleanup
        o_proc.wait(20)
        h.do(do="prompt", text="next step")                                     # the Core is gone: spooled +
        p = h.expect("prompt")                                                  # delivered from agents.db
        assert "please run the tests" in p["output"]["hookSpecificOutput"]["additionalContext"]
        h.do(do="stop", text="tests green")
        assert h.expect("stop")["output"] is None                               # not twice
        assert list((home / "agents" / "spool").glob("*.json"))                 # events kept for the restart
        o2 = svc.Orchestrator(home, cfg).start()
        try:
            assert not list((home / "agents" / "spool").glob("*.json"))         # replayed once
            s = o2.db.session(sid)
            assert s["last_summary"] == "tests green"                           # the stop seen while down counts
            kinds = [e["kind"] for e in o2.db.events(sid)]
            assert kinds.count("status_snapshot") == 1
            v = o2.list(force=True)["sessions"][0]
            assert v["state"] == "IDLE" and v["id"] == sid
            assert o2.db.pending(sid) == []
        finally:
            o2.stop()
    finally:
        h.end()
        if o_proc.poll() is None:
            o_proc.kill()


def test_idle_bridge_and_registry_cost(agent_env):
    """Acceptance: the bridge + registry idle at most 0.2 % CPU and 15 MB RAM; no polling loop."""
    home = agent_env["home"]
    cfg = agents_cfg()
    p = subprocess.Popen([sys.executable, str(FAKES / "orch_proc.py"), str(home), json.dumps(cfg.agents.claude_command),
                          json.dumps(cfg.agents.codex_command), "--measure", "10"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True, env=dict(os.environ, PYTHONPATH=SRC))
    try:
        assert json.loads(p.stdout.readline())["ready"]
        m = json.loads(p.stdout.readline())
    finally:
        p.stdin.close()
        p.wait(30)
    print("idle measurement", m)
    assert m["cpu_s"] / m["wall_s"] <= 0.002, m
    assert m["rss_delta_mb"] <= 15, m
