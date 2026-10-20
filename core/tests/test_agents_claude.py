"""SW-V4 E (#49) UC1 / UC2 / UC5 with Claude Code sessions the owner STARTED BY HAND (fake interactive Claude Code
host processes running the real JARVES plugin: hooks in exec form + the stdio MCP server as their child, over the
real named pipe to a real Orchestrator; the fake `claude agents --json` lists them).

Covered: discovery without the plugin (observe only, NO_LIVE_CHANNEL), plugin registration via SessionStart,
status from the snapshot (a busy worker is not interrupted), ask without a channel = queued and delivered once at
the next Stop with the reply through jarves_reply, live channel push + reply, UserPromptSubmit delivery,
agent-initiated status reports, approvals through the approvals service (approve / deny / typed code for a risky
command / nobody answers = the terminal dialog decides), spoofed session ids refused, the allow-list."""
from __future__ import annotations

import json
import time
import uuid

import pytest

from agents_helpers import Host, Owner, agent_env, agents_cfg, make_orch, plugin_dir, wait_for  # noqa: F401
from jarves.orchestrator import claude as cc
from jarves.orchestrator import service as svc


@pytest.fixture
def world(agent_env):
    home = agent_env["home"]
    cfg = agents_cfg(permission_wait_s=20)
    owner = Owner(home)
    o = make_orch(home, cfg, owner)
    o.projects.add(str(agent_env["projects"]["alpha"]), "alpha", ["אלפא"])
    o.projects.add(str(agent_env["projects"]["beta"]), "beta")
    hosts: list[Host] = []

    def host(project="alpha", **kw) -> Host:
        h = Host(agent_env["projects"][project] if project in agent_env["projects"] else agent_env["outside"],
                 plugin_dir(home), session_id=kw.pop("session_id", str(uuid.uuid4())), **kw)
        hosts.append(h)
        return h
    yield {"o": o, "owner": owner, "host": host, "env": agent_env, "home": home, "cfg": cfg}
    for h in hosts:
        h.end()
    o.stop()
    owner.stop()


def test_manual_session_without_plugin_is_observed_honestly(agent_env):
    """No plugin: `claude agents --json` alone - state and age, NO_LIVE_CHANNEL for asks; nothing is sent."""
    from agents_helpers import FAKES
    import subprocess
    import sys
    home = agent_env["home"]
    o = make_orch(home, agents_cfg())
    o.projects.add(str(agent_env["projects"]["alpha"]), "alpha")
    p = subprocess.Popen([sys.executable, "-c", "import sys, json, os, time; sys.path.insert(0, sys.argv[1]); "
                          "import fake_claude as f; f.register({'pid': os.getpid(), 'sessionId': 'manual-1', "
                          "'cwd': os.getcwd(), 'kind': 'interactive', 'startedAt': int(time.time()*1000), "
                          "'name': 'alpha-2f', 'status': 'busy'}); sys.stdin.read()", str(FAKES)],
                         cwd=str(agent_env["projects"]["alpha"]), stdin=subprocess.PIPE)
    try:
        r = wait_for(lambda: (o.list(force=True)["sessions"] or None), what="the manual session listed")
        s = r[0]
        assert s["id"] == "claude:manual-1" and s["state"] == "RUNNING" and s["attach"] == "observe_only"
        assert s["capabilities"] == ["observe"]
        a = o.ask("alpha", "what are you doing right now?")
        assert a["source"] == "snapshot" and "Claude in alpha: working" in a["text"]
        t = o.tell("alpha", "run the tests")
        assert t["ok"] is False and t["code"] == "NO_LIVE_CHANNEL" and "install-plugin" in t["text"]
        assert o.db.pending() == []
    finally:
        p.stdin.close()
        p.wait(20)
        o.stop()
    o2 = svc.Orchestrator(home, agents_cfg(), serve=False)
    gone = o2.list(force=True, include_stopped=True)["sessions"][0]
    assert gone["state"] == "STOPPED"                                   # never "running" from an old row
    o2.db.close()


def test_plugin_session_status_ask_at_stop_and_reply(world):
    o, h = world["o"], world["host"](auto_reply=True, name="alpha-main")
    start = h.expect("session_start")
    assert "JARVES" in json.dumps(start["output"])                     # SessionStart context
    mcp = h.expect("mcp")
    assert mcp["protocol"] != "2026-07-28"                              # a channel must stay on the older revision
    assert set(mcp["tools"]) == {"jarves_reply", "jarves_report_status", "jarves_heartbeat", "jarves_inbox"}
    assert "claude/channel" in mcp["capabilities"]["experimental"]
    sid = f"claude:{h.session_id}"
    wait_for(lambda: (o.db.session(sid) or {}).get("plugin"), what="registered by SessionStart")
    h.do(do="prompt", text="refactor the bridge module")
    h.expect("prompt")
    h.do(do="tool", file="core/bridge.py")
    h.expect("tool")
    st = o.status("alpha")
    assert st["ok"] and st["source"] == "snapshot" and "bridge.py" in st["text"]
    # a status question while it works: answered from the snapshot, nothing queued for the worker
    a = o.ask("alpha", "מה המצב?")
    assert a["source"] == "snapshot" and a["text"].startswith("קלוד בפרויקט alpha") and not o.db.pending(sid)
    # a real question: no live channel -> queued for the next safe stop (Stop hook), reply comes via jarves_reply
    q = o.ask("alpha", "what is blocking you?")
    assert q["code"] == "QUEUED" and q["delivery"] == "stop_hook" and "NO_LIVE_CHANNEL" not in q["code"]
    h.do(do="stop", text="Refactor done; 12 tests pass.")
    stop = h.expect("stop")
    assert stop["output"]["decision"] == "block" and "The owner asks: what is blocking you?" in stop["output"]["reason"]
    assert h.expect("replied")["via"] == "stop"
    rep = o.wait_reply(q["correlation_id"], 30)
    assert rep and "tests pass" in rep["text"]
    assert h.expect("stop2")["output"] is None                         # delivered ONCE: nothing on the next stop
    s = o.db.session(sid)
    assert s["state"] == "IDLE" and s["last_summary"] == "answered the owner"     # the latest Stop's own words
    kinds = [e["kind"] for e in o.db.events(sid)]
    assert "delivered" in kinds and "reply" in kinds


def test_live_channel_push_and_reply(world):
    o, h = world["o"], world["host"](channel=True, auto_reply=True)
    h.expect("session_start")
    sid = f"claude:{h.session_id}"
    wait_for(lambda: sid in o.channels and o.channels[sid].meta.get("channel_live"), what="live channel linked")
    r = o.ask("alpha", "what is blocking you?", wait_s=30)
    assert r["delivery"] == "channel" and r["source"] == "live", r
    assert "refactoring the bridge" in r["text"]
    ch = h.expect("channel")
    assert ch["meta"]["from"] == "jarves" and "correlation_id" in ch["meta"]
    assert o.db.pending(sid) == []                                     # acked by the channel server


def test_tell_reaches_the_next_prompt_and_inbox(world):
    o, h = world["o"], world["host"]()
    h.expect("session_start")
    sid = f"claude:{h.session_id}"
    wait_for(lambda: (o.db.session(sid) or {}).get("plugin"), what="registered")
    t = o.tell("alpha", "please run the targeted tests")
    assert t["code"] == "QUEUED"
    h.do(do="prompt", text="next")
    p = h.expect("prompt")
    ctx = p["output"]["hookSpecificOutput"]["additionalContext"]
    assert "The owner says: please run the targeted tests" in ctx
    o.tell("alpha", "and then commit")
    h.do(do="inbox")
    assert "and then commit" in h.expect("inbox")["result"]["content"][0]["text"]


def test_agent_initiated_status_report_and_owner_signal(agent_env):
    home = agent_env["home"]
    signals = []
    o = make_orch(home, agents_cfg(), notify=lambda en, he, kind: signals.append((kind, en, he)))
    o.projects.add(str(agent_env["projects"]["alpha"]), "alpha")
    h = Host(agent_env["projects"]["alpha"], plugin_dir(home), session_id=str(uuid.uuid4()))
    try:
        h.expect("session_start")
        sid = f"claude:{h.session_id}"
        wait_for(lambda: (o.db.session(sid) or {}).get("plugin"), what="registered")
        h.do(do="report", args={"summary": "blocked: the CI token is missing", "status": "blocked",
                                "blocker": "CI token", "next_step": "ask the owner"})
        assert "noted" in h.expect("reported")["result"]["content"][0]["text"]
        s = o.db.session(sid)
        assert s["blocker"] == "CI token" and s["next_step"] == "ask the owner"
        wait_for(lambda: any(k == "blocked" for k, _, _ in signals), what="blocked signal")
        kind, en, he = [x for x in signals if x[0] == "blocked"][0]
        assert en == "Claude in alpha is blocked." and he == "קלוד בפרויקט alpha תקוע."
        dg = o.digest(since=time.time() - 3600)
        assert dg["blocked"] == ["alpha"] and "1 blocked" in dg["text"]
    finally:
        h.end()
        o.stop()


@pytest.mark.parametrize("policy,command,expect", [
    ("approve", "npm test", "allow"),
    ("deny", "npm test", "deny"),
    ("approve", "git push origin main", None),       # risky: the owner must TYPE the code; a click is not enough
    ("code", "git push origin main", "allow"),
])
def test_permission_request_goes_to_the_owner(world, policy, command, expect):
    world["owner"].policy = policy
    o, h = world["o"], world["host"]()
    h.expect("session_start")
    sid = f"claude:{h.session_id}"
    wait_for(lambda: (o.db.session(sid) or {}).get("plugin"), what="registered")
    t0 = time.monotonic()
    h.do(do="permission", command=command)
    owner = world["owner"]
    with owner.cv:
        assert owner.cv.wait_for(lambda: owner.asked, 30)
    asked_at, a = owner.asked[-1]
    assert asked_at - t0 < 2.0 + 3.0                                     # popup within 2 s of the hook (+ CI slack)
    assert a.source == "agent" and a.voice_ok is False                  # screen only, never the model
    assert a.details["project"] == "alpha" and command in a.prompt
    assert bool(a.typed_code) == ("git push" in command)
    r = h.expect("permission")
    if expect is None:                                                   # the wrong answer is refused -> timeout ->
        assert r["decision"] is None                                     # no decision: Claude's own dialog decides
    else:
        assert r["decision"] == expect
    row = o.db.approvals(None)[0]
    assert row["session_id"] == sid and row["tool"] == "Bash"
    assert [x["what"] for x in o.db.audit_rows(sid)][0] == "approval"


@pytest.mark.windows_only
@pytest.mark.parametrize("invoke,expect", [("approve", "allow"), ("wrong-code", "deny"), ("deny", "deny")])
def test_real_approvals_popup_decides_a_risky_agent_command(world, invoke, expect):
    """The existing approvals popup (approval_popup.py, Tk, own process, invisible test window): the risky command
    needs the typed code; a wrong code cannot approve it."""
    from jarves import approvals as appr
    owner = world["owner"]
    owner.svc.channels[:] = [appr.PopupChannel(owner.svc, test_invoke=invoke)]
    o, h = world["o"], world["host"]()
    h.expect("session_start")
    wait_for(lambda: (o.db.session(f"claude:{h.session_id}") or {}).get("plugin"), what="registered")
    h.do(do="permission", command="git push --force origin main")
    r = h.expect("permission", timeout=90)
    assert r["decision"] == expect
    hist = appr.read_history(world["home"], 5)
    assert hist[0]["channel"] == "popup" and hist[0]["source"] == "agent"


def test_nobody_answers_leaves_the_terminal_dialog(world):
    world["owner"].policy = None
    world["o"].acfg.permission_wait_s = 5
    o, h = world["o"], world["host"]()
    h.expect("session_start")
    wait_for(lambda: (o.db.session(f"claude:{h.session_id}") or {}).get("plugin"), what="registered")
    h.do(do="permission", command="pytest -q")
    r = h.expect("permission")
    assert r["decision"] is None                                         # never an automatic approval


def test_spoofed_session_id_is_refused(world):
    o = world["o"]
    a, b = world["host"](), world["host"]("beta")
    a.expect("session_start")
    b.expect("session_start")
    sa, sb = f"claude:{a.session_id}", f"claude:{b.session_id}"
    wait_for(lambda: (o.db.session(sa) or {}).get("pid") and (o.db.session(sb) or {}).get("pid"), what="pids")
    # session B's hook claims to be session A asking for a permission
    b.do(do="hook", event="PermissionRequest", payload={"session_id": a.session_id, "cwd": str(world["env"]["projects"]["alpha"]),
                                                        "tool_name": "Bash", "tool_input": {"command": "git push"}})
    r = b.expect("hook")
    assert r["output"] is None
    assert any(x["what"] == "spoof_refused" for x in o.db.audit_rows(sa))
    assert world["owner"].asked == []                                    # nothing reached the owner


def test_sessions_outside_the_allow_list_are_counted_not_shown(world):
    o = world["o"]
    h = world["host"]("outside")
    h.expect("session_start")
    wait_for(lambda: o.list(force=True)["hidden_outside_allowlist"] == 1, what="counted")
    assert o.list()["sessions"] == []
    assert o.tell(f"claude:{h.session_id}", "x")["code"] == "NO_SUCH_SESSION"
    h.do(do="permission", command="ls")
    assert h.expect("permission")["decision"] is None                    # not allow-listed: JARVES stays out
    assert world["owner"].asked == []


def test_ambiguous_reference_asks_which(world):
    o = world["o"]
    h1, h2 = world["host"](), world["host"]()
    h1.expect("session_start")
    h2.expect("session_start")
    wait_for(lambda: len(o.list(force=True)["sessions"]) == 2, what="two sessions")
    r = o.tell("alpha", "continue")
    assert r["code"] == "AMBIGUOUS_SESSION" and len(r["candidates"]) == 2
    assert o.tell(h1.session_id, "continue")["ok"]


def test_plugin_files_and_install_through_the_cli(world):
    home = world["home"]
    pdir = cc.write_plugin(home)
    hooks = json.loads((pdir / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == set(cc.HOOK_EVENTS)
    for blocks in hooks.values():
        h = blocks[0]["hooks"][0]
        assert h["type"] == "command" and h["args"][0] == "-c"            # exec form: no shell quoting
    mcp = json.loads((pdir / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["jarves"]
    assert "--home" in mcp["args"]
    r = world["o"].install_plugin("claude", approved=True)
    assert r["ok"] and r["plugin"] == "jarves@jarves-local"
    assert world["o"].doctor()["checks"][1] == {"check": "claude_plugin", "ok": True, "detail": "installed"}
    r = world["o"].install_plugin("claude", approved=True, uninstall=True)
    assert r["ok"]
    world["owner"].policy = "deny"
    assert world["o"].install_plugin("claude")["code"] == "NOT_APPROVED"  # the owner said no on the screen
