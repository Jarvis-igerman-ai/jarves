"""SW-V4 E (#49) UC2 / UC3 / UC4: sessions JARVES starts and steers - Codex through the App Server (fake that refuses
anything the recorded 0.163.0 schema would refuse) and Claude Code through `claude -p` stream-json (fake that runs
the real plugin's hooks). Real child processes, real pipes, real git worktrees, a real approvals service.

Covered: on-screen approval before a start (denied = nothing started), a new worktree per start, one writer per
folder, completion events, steer / interrupt, approvals routed to the owner (accept / decline, never
acceptForSession), externally started Codex threads = observe + UNSUPPORTED_ATTACH + resume offer, API-key
accounts refused (AUTH_REQUIRED), daily turn cap (BUDGET_REACHED), cost / token usage recorded, never a
permission-skipping flag, and two providers in three projects at once without collisions."""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from agents_helpers import Host, Owner, agent_env, agents_cfg, make_orch, plugin_dir, wait_for  # noqa: F401
from jarves.orchestrator import policy


@pytest.fixture
def world(agent_env):
    home = agent_env["home"]
    cfg = agents_cfg(permission_wait_s=20)
    owner = Owner(home)
    o = make_orch(home, cfg, owner)
    for n in ("alpha", "beta", "gamma"):
        o.projects.add(str(agent_env["projects"][n]), n)
    yield {"o": o, "owner": owner, "env": agent_env, "home": home}
    o.stop()
    owner.stop()


def _state(o, sid, *states):
    return wait_for(lambda: (o.db.session(sid) or {}).get("state") in states and o.db.session(sid),
                    what=f"{sid} in {states}")


# ---------------------------------------------------------------------- Codex
def test_codex_start_needs_the_owner_and_runs_in_a_new_worktree(world):
    o, owner = world["o"], world["owner"]
    owner.policy = "deny"
    r = o.start_session("codex", "alpha", "add a health check")
    assert r["code"] == "NOT_APPROVED" and not o.managed
    owner.policy = "approve"
    r = o.start_session("codex", "alpha", "add a health check")
    assert r["ok"], r
    s = r["session"]
    assert s["origin"] == "managed" and s["auth"] == "subscription" and s["attach"] == "managed"
    wt = Path(s["cwd"])
    assert wt.parent.parent.name == ".jarves" and wt.is_dir() and policy.inside(str(wt), str(world["env"]["projects"]["alpha"]))
    branches = subprocess.run(["git", "-C", str(world["env"]["projects"]["alpha"]), "branch"], capture_output=True,
                              text=True).stdout
    assert "jarves/" in branches
    a = owner.asked[0][1]
    assert a.title == "agents_start" and a.voice_ok is False                  # OD-23: on-screen only
    done = _state(o, s["id"], "IDLE")
    assert "src/fake.py" in json.dumps(done["files"]) and done["tokens_total"] == 150
    assert "Codex worked on: add a health check" in done["last_summary"]
    assert any(e["kind"] == "completed" for e in o.db.events(s["id"]))


def test_codex_steer_and_interrupt(world):
    o = world["o"]
    r = o.start_session("codex", "beta", "SLOW refactor")
    sid = r["session"]["id"]
    _state(o, sid, "RUNNING")
    wait_for(lambda: any(e["kind"] == "progress" for e in o.db.events(sid)), what="first item")
    t = o.tell(sid, "also update the docs")
    assert t["code"] == "SENT" and t["delivery"] == "turn"
    s = _state(o, sid, "IDLE")
    assert "steered: also update the docs" in s["last_summary"]
    o.tell(sid, "SLOW second task")
    _state(o, sid, "RUNNING")
    wait_for(lambda: o.managed[sid].busy and o.managed[sid].turn_id, what="turn id")
    assert o.interrupt(sid)["ok"]
    _state(o, sid, "IDLE")
    assert any(e["kind"] == "interrupted" for e in o.db.events(sid))


@pytest.mark.parametrize("policy_,expect", [("code", "ran: git push origin main"),
                                            ("deny", "declined: git push origin main")])
def test_codex_approval_request_goes_to_the_owner(world, policy_, expect):
    o, owner = world["o"], world["owner"]
    r = o.start_session("codex", "gamma", "x", approved=True)
    sid = r["session"]["id"]
    _state(o, sid, "IDLE")                              # (while a turn runs, a tell is a steer of that turn)
    owner.policy = policy_
    assert o.tell(sid, "RUN: git push origin main")["code"] == "SENT"
    s = wait_for(lambda: expect in ((o.db.session(sid) or {}).get("last_summary") or "") and o.db.session(sid),
                 what=expect)
    a = owner.asked[-1][1]
    assert a.typed_code and "git push" in a.prompt and a.details["provider"] == "codex"   # risky: typed code
    row = o.db.approvals(None)[0]
    assert row["decision"] == ("allow" if policy_ == "code" else "deny") and row["risk"] == "high"
    assert s["state"] == "IDLE"


def test_external_codex_thread_is_observed_not_attached(world):
    env = world["env"]
    tid = str(uuid.uuid4())
    (env["tmp"] / "fake_codex" / "threads.json").write_text(json.dumps(
        [{"id": tid, "cwd": str(env["projects"]["alpha"]), "source": "vscode", "preview": "fix the parser"}]),
        encoding="utf-8")
    o = world["o"]
    rows = o.list(force=True)["sessions"]
    s = [x for x in rows if x["id"] == f"codex:{tid}"][0]
    assert s["attach"] == "UNSUPPORTED_ATTACH" and s["state"] == "DISCOVERED" and "resume" in s["capabilities"]
    t = o.tell(f"codex:{tid}", "continue")
    assert t["code"] in ("UNSUPPORTED_ATTACH", "NO_SUCH_SESSION")
    t = o.ask(tid, "what are you doing?")
    assert t["source"] == "snapshot"
    world["owner"].policy = "deny"
    assert o.resume(tid)["code"] == "NOT_APPROVED"
    world["owner"].policy = "approve"
    r = o.resume(tid)
    assert r["ok"] and r["session"]["attach"] == "managed"                    # resumed under JARVES after a yes
    assert o.tell(tid, "continue the parser fix")["code"] == "SENT"


def test_codex_api_key_account_is_refused(world, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_ACCOUNT", "apiKey")
    r = world["o"].start_session("codex", "alpha", "x", approved=True)
    assert r["code"] == "AUTH_REQUIRED" and "API billing" in r["text"] or "API" in r["text"]
    assert not world["o"].managed


# ---------------------------------------------------------------------- Claude Code (claude -p)
def test_claude_managed_start_tell_interrupt_stop_and_cost(world):
    o = world["o"]
    r = o.start_session("claude", "alpha", "add logging", approved=True)
    assert r["ok"], r
    sid = r["session"]["id"]
    assert sid[:8] in r["text"]                                # #362: the CLI/chat text names the session id
    s = _state(o, sid, "IDLE")
    assert s["auth"] == "subscription" and s["origin"] == "managed" and "Working on: add logging" in s["last_summary"]
    assert s["cost_usd"] == pytest.approx(0.01) and s["tokens_total"] == 120
    t = o.tell(sid, "now run the targeted tests")
    assert t["code"] == "SENT"
    wait_for(lambda: "run the targeted tests" in (o.db.session(sid)["last_summary"] or ""), what="second turn")
    assert o.db.session(sid)["cost_usd"] == pytest.approx(0.02)
    o.tell(sid, "SLOW big step")
    wait_for(lambda: "starting a long step" in (o.db.session(sid)["last_activity"] or ""), what="long step")
    assert o.pause(sid)["ok"]
    s = _state(o, sid, "PAUSED")
    wait_for(lambda: "(interrupted)" in (o.db.session(sid)["last_summary"] or ""), what="interrupted")
    assert o.db.session(sid)["state"] == "PAUSED"
    assert o.continue_(sid)["code"] == "SENT"
    world["owner"].policy = "deny"
    assert o.stop_session(sid)["code"] == "NOT_APPROVED"
    world["owner"].policy = "approve"
    assert o.stop_session(sid)["ok"]
    assert _state(o, sid, "STOPPED")
    assert o.db.count("turn") >= 4
    kinds = [x["what"] for x in o.db.audit_rows(sid)]
    assert "start" in kinds and "stop" in kinds and "pause" in kinds


def test_claude_managed_permission_through_the_plugin_hook(world):
    o, owner = world["o"], world["owner"]
    r = o.start_session("claude", "beta", "x", approved=True)
    sid = r["session"]["id"]
    _state(o, sid, "IDLE")
    owner.policy = "approve"
    o.tell(sid, "APPROVE: pytest -q tests/unit")
    wait_for(lambda: "ran: pytest" in (o.db.session(sid)["last_summary"] or ""), what="approved command ran")
    owner.policy = "deny"
    o.tell(sid, "APPROVE: rm -rf build")
    wait_for(lambda: "denied: rm -rf build" in (o.db.session(sid)["last_summary"] or ""), what="denied command")
    assert owner.asked[-1][1].typed_code                                    # deletion = typed code class


def test_claude_api_key_session_is_refused(world, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_API_KEY", "1")
    r = world["o"].start_session("claude", "alpha", "x", approved=True)
    assert r["code"] == "AUTH_REQUIRED" and not world["o"].managed


def test_one_writer_per_folder_and_new_worktree_offer(world):
    env, o = world["env"], world["o"]
    h = Host(env["projects"]["alpha"], plugin_dir(world["home"]), session_id=str(uuid.uuid4()))
    try:
        h.expect("session_start")
        wait_for(lambda: o.list(force=True)["sessions"], what="manual session")
        r = o.start_session("claude", "alpha", "x", approved=True, here=True)
        assert r["code"] == "SECOND_WRITER" and r["offer"] == "worktree"
        r = o.start_session("claude", "alpha", "x", approved=True)              # a new worktree is fine
        assert r["ok"] and r["session"]["cwd"] != str(env["projects"]["alpha"])
        assert o.resume(h.session_id, approved=True)["code"] == "SECOND_WRITER"  # never two writers on one session
    finally:
        h.end()


def test_daily_turn_cap_and_never_skip_permissions(world):
    o = world["o"]
    o.acfg.max_turns_per_day = 1
    r = o.start_session("codex", "alpha", "first", approved=True)
    assert r["ok"]
    sid = r["session"]["id"]
    _state(o, sid, "IDLE")
    t = o.tell(sid, "second")
    assert t["code"] == "BUDGET_REACHED"
    assert o.start_session("claude", "beta", "x", approved=True)["code"] == "BUDGET_REACHED"
    with pytest.raises(PermissionError):
        policy.check_flags(["claude", "-p", "--dangerously-skip-permissions"])
    with pytest.raises(PermissionError):
        policy.check_flags(["codex", "exec", "--dangerously-bypass-approvals-and-sandbox"])


def test_two_providers_three_projects_digest_and_no_collisions(world):
    """UC4: 2 Claude Code sessions (one by hand, one started) + 1 Codex thread in 3 projects at once."""
    env, o = world["env"], world["o"]
    h = Host(env["projects"]["alpha"], plugin_dir(world["home"]), session_id=str(uuid.uuid4()))
    try:
        h.expect("session_start")
        c = o.start_session("claude", "beta", "write docs", approved=True)
        x = o.start_session("codex", "gamma", "RUN: git push", approved=True)
        assert c["ok"] and x["ok"]
        world["owner"].policy = None                                           # the Codex approval waits
        rows = wait_for(lambda: (len(o.list(force=True)["sessions"]) == 3) and o.list()["sessions"], what="3 sessions")
        assert {s["project"] for s in rows} == {"alpha", "beta", "gamma"}
        assert {s["provider"] for s in rows} == {"claude", "codex"}
        wait_for(lambda: o.db.session(x["session"]["id"])["state"] == "WAITING_APPROVAL", what="codex waits")
        dg = o.digest(since=time.time() - 3600)
        assert dg["blocked"] == ["gamma"] and "3 session(s)" in dg["text"]
        he = o.digest(since=time.time() - 3600, lang="he")
        assert "חסומים" in he["text"]
        assert o.tell("codex", "x")["code"] in ("SENT", "AMBIGUOUS_SESSION")   # one codex: resolved by provider
        assert o.tell("claude", "x")["code"] == "AMBIGUOUS_SESSION"            # two claude: asks which
    finally:
        h.end()


def test_core_not_running_message_names_the_port_clash_fix(world):
    o = world["o"]
    o.serve = None
    r = o.start_session("claude", "alpha", "x", approved=True)
    assert r["code"] == "CORE_NOT_RUNNING" and "[link] port" in r["text"] and "jarvis start" in r["text"]


def test_claude_launch_carries_the_remaining_daily_cap_as_max_budget(world):
    """#362: the daily cost cap was checked only when a turn started; a single long turn could pass it."""
    from jarves.orchestrator import claude
    m = claude.ManagedClaude(["claude"], ".", plugin_dir=None, on_event=lambda k, d: None, max_budget_usd=2.5)
    assert m.argv[m.argv.index("--max-budget-usd") + 1] == "2.50"
    assert "--max-budget-usd" not in claude.ManagedClaude(["claude"], ".", plugin_dir=None, on_event=lambda k, d: None).argv
    o = world["o"]
    cap = o.acfg.max_cost_usd_per_day
    assert cap > 0 and abs(o._budget_left() - cap) < 0.01
