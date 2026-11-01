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


@pytest.mark.parametrize("provider,model", [("claude", "sonnet"), ("codex", "owner-selected-model")])
def test_explicit_model_crosses_dispatch_approval_and_real_fake_provider(world, provider, model):
    o, owner = world["o"], world["owner"]
    r = o.call("start", provider=provider, project="alpha", task="Review selected task", model=model)
    assert r["ok"], r
    sid = r["session"]["id"]
    assert r["session"]["requested_model"] == model
    approval = owner.asked[0][1]
    assert approval.details["requested_model"] == model and model in approval.prompt
    if provider == "claude":
        argv = o.managed[sid].argv
        assert argv[argv.index("--model") + 1] == model
    _state(o, sid, "IDLE")


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


# ---------------------------------------------------------------------- workflow autopilot owner grant (T2, Tier R)
M_SON, M_OPUS = "claude-sonnet-5-5", "claude-opus-5-5"


def _grant(o, owner, **kw):
    owner.policy = "code"
    args = dict(project="alpha", models=[M_SON, M_OPUS], days=2, max_concurrent=1)
    args.update(kw)
    return o.autopilot_op("grant", by="cli", **args)


def test_autopilot_grant_needs_the_typed_code(world):
    o, owner = world["o"], world["owner"]
    owner.policy = "approve"                              # a plain yes without the code is not enough
    r = o.autopilot_op("grant", by="cli", project="alpha", models=[M_SON])
    assert r["code"] == "NOT_APPROVED" and o.autopilot_grant() is None
    assert _grant(o, owner, project="alpha")["ok"] is True            # "code" policy types the shown code
    a = owner.asked[-1][1]
    assert a.title == "agents_autopilot_grant" and a.typed_code and a.voice_ok is False
    owner.policy = "deny"
    o.autopilot_op("revoke", by="cli")
    assert o.autopilot_op("grant", by="cli", project="alpha", models=[M_SON])["code"] == "NOT_APPROVED"
    assert o.autopilot_grant() is None
    assert o.autopilot_op("grant", by="autopilot", project="alpha", models=[M_SON])["code"] == "NOT_APPROVED"
    for bad in ({"days": 366}, {"days": 0}, {"max_concurrent": 99}, {"models": ["a", "b", "c", "d"]}, {"models": []}):
        assert not _grant(o, owner, **bad)["ok"]
    assert not _grant(o, owner, project="nope")["ok"]


def test_autopilot_grant_lifetime_default_max_and_revoke(world):
    o, owner = world["o"], world["owner"]
    owner.policy = "code"
    r = o.autopilot_op("grant", by="cli", project="alpha", models=[M_SON])      # no days = the 365-day default
    assert r["ok"] and abs((r["grant"]["expires_at"] - r["grant"]["granted_at"]) / 86400 - 365) < 1e-6
    assert o.GRANT_MAX_DAYS == 365.0
    assert _grant(o, owner, days=365)["ok"]
    assert not _grant(o, owner, days=366)["ok"] and not _grant(o, owner, days=1000)["ok"]
    assert o.autopilot_grant() is not None                                       # the refused ones changed nothing
    assert o.autopilot_op("revoke", by="cli")["ok"] and o.autopilot_grant() is None   # revoke ends it at once
    assert _grant(o, owner, days=365)["ok"]
    o.clock = lambda t=o.clock() + 366 * 86400: t                                # expiry is still enforced
    assert o.autopilot_grant() is None


def test_autopilot_grant_exact_match(world):
    o, owner = world["o"], world["owner"]
    task = "x"
    assert _grant(o, owner)["ok"]
    n = len(owner.asked)
    owner.policy = "deny"                                  # any popup would now be denied
    r = o.start_session("claude", "alpha", task, by="autopilot", model=M_SON, env={"JARVES_WORKFLOW_AGENT": "ap-bugs-1"})
    assert r["ok"], r
    assert len(owner.asked) == n                           # no popup on an exact match
    sid = r["session"]["id"]
    assert (o.db.session(sid)["meta"] or {}).get("autopilot") is True
    # over max_concurrent (1): refused, no popup
    assert o.start_session("claude", "alpha", task, by="autopilot", model=M_OPUS)["code"] == "BUDGET_REACHED"
    # a different model / project / here / no model / other provider: popup (denied here) or refusal
    assert o.start_session("claude", "alpha", task, by="autopilot", model="claude-haiku-5-5")["code"] == "NOT_APPROVED"
    assert o.start_session("claude", "beta", task, by="autopilot", model=M_SON)["code"] == "NOT_APPROVED"
    assert o.start_session("claude", "alpha", task, by="autopilot")["code"] == "NOT_APPROVED"
    assert o.start_session("claude", "alpha", task, by="autopilot", model=M_SON, here=True)["code"] in (
        "NOT_APPROVED", "BUDGET_REACHED")
    for prov in ("codex", "opencode"):
        assert o.start_session(prov, "alpha", task, by="autopilot", model=M_SON)["ok"] is False
    assert not o.start_session("claude", "alpha", task, by="autopilot", model=M_SON,
                               env={"PATH": "/evil"}).get("ok")
    # a non-autopilot caller never gets the grant, and cannot pass env
    assert o.start_session("claude", "alpha", task, by="owner", model=M_OPUS)["code"] == "NOT_APPROVED"
    # stop: only the session the autopilot started
    assert o.stop_session(sid, by="autopilot")["ok"]
    owner.policy = "deny"
    h = o.start_session("claude", "beta", task, approved=True)
    assert h["ok"]
    assert o.stop_session(h["session"]["id"], by="autopilot")["code"] == "NOT_APPROVED"
    o.stop_session(h["session"]["id"], approved=True)


def test_grant_expiry(world):
    o, owner = world["o"], world["owner"]
    assert _grant(o, owner)["ok"]
    owner.policy = "deny"
    now = o.clock()
    o.clock = lambda: now + 3 * 86400
    assert o.autopilot_grant() is None
    assert o.start_session("claude", "alpha", "x", by="autopilot", model=M_SON)["code"] == "NOT_APPROVED"
    o.clock = lambda: now
    assert o.autopilot_grant() is not None


def test_grant_never_covers_tool_approvals(world):
    o, owner = world["o"], world["owner"]
    assert _grant(o, owner)["ok"]
    owner.policy = "approve"
    r = o.start_session("claude", "alpha", "x", by="autopilot", model=M_SON)
    assert r["ok"], r
    sid = r["session"]["id"]
    before = len(owner.asked)
    o._approval(o.db.session(sid), "req-1", tool="Bash", command="git push --force origin main", cwd=r["session"]["cwd"])
    assert len(owner.asked) > before                       # the tool request still reaches the owner
    o.stop_session(sid, by="autopilot")


class _FakeTTY:
    def __init__(self, tty=True):
        self.tty, self.out = tty, []

    def isatty(self):
        return self.tty

    def write(self, s):
        self.out.append(s)
        return len(s)

    def flush(self):
        pass


def _terminal_grant(world, monkeypatch, tty, answer):
    """The CLI path with no Core: the typed code is asked in the terminal. answer(code) -> what the owner types."""
    import builtins
    import sys
    from jarves import approvals as appr
    o = world["o"]
    out = _FakeTTY(tty)
    monkeypatch.setattr(sys, "stdin", _FakeTTY(tty))
    monkeypatch.setattr(sys, "stdout", out)

    def fake_input(prompt=""):
        code = next(x for x in "".join(out.out).split() if len(x) == 4 and x.isdigit())
        return answer(code)
    monkeypatch.setattr(builtins, "input", fake_input)
    ask = appr.terminal_code_ask(world["home"])
    if ask is not None:
        o.terminal_ask = ask
    return o.autopilot_op("grant", by="cli", project="alpha", models=[M_SON], days=2, max_concurrent=1), ask


def test_autopilot_grant_terminal_code_right_wrong_and_no_tty(world, monkeypatch):
    o = world["o"]
    world["owner"].policy = "deny"                       # no popup channel would ever approve
    r, ask = _terminal_grant(world, monkeypatch, True, lambda code: "0000" if code != "0000" else "1111")
    assert ask is not None and r["code"] == "NOT_APPROVED" and o.autopilot_grant() is None     # wrong code
    r, _ = _terminal_grant(world, monkeypatch, True, lambda code: code)
    assert r["ok"] and o.autopilot_grant() is not None                                           # right code
    audit = (world["home"] / "approvals" / "audit.jsonl").read_text(encoding="utf-8")
    assert '"channel": "terminal"' in audit and '"decision": "approved"' in audit and '"decision": "denied"' in audit
    o.autopilot_op("revoke", by="cli")
    o.terminal_ask = None
    r, ask = _terminal_grant(world, monkeypatch, False, lambda code: code)                      # not a TTY: refused
    assert ask is None and r["code"] == "NOT_APPROVED" and o.autopilot_grant() is None
    o.terminal_ask = lambda *a, **k: True
    assert o.autopilot_op("grant", by="autopilot", project="alpha", models=[M_SON])["code"] == "NOT_APPROVED"
    assert o.autopilot_op("grant", by="voice", project="alpha", models=[M_SON])["code"] == "NOT_APPROVED"


def test_autopilot_grant_keeps_typed_code_without_a_core(world, monkeypatch):
    """No Core and no TTY: the fallback must still demand the typed code (no downgrade to a plain yes)."""
    from jarves import approvals as appr
    o = world["o"]
    seen = {}
    monkeypatch.setattr(o, "approvals", None)
    monkeypatch.setattr(appr, "current", lambda: None)
    monkeypatch.setattr(appr, "ask_owner", lambda home, prompt, **kw: (seen.update(kw) or (False, "")))
    assert o.autopilot_op("grant", by="cli", project="alpha", models=[M_SON])["code"] == "NOT_APPROVED"
    assert seen.get("typed") is True


def test_autopilot_kill_switch_ends_the_grant(world):
    o, owner = world["o"], world["owner"]
    assert _grant(o, owner)["ok"]
    owner.policy = "deny"
    wf = world["home"] / "workflow"
    wf.mkdir(exist_ok=True)
    (wf / "autopilot.json").write_text('{"kill": true}', encoding="utf-8")
    assert o.start_session("claude", "alpha", "x", by="autopilot", model=M_SON)["code"] == "NOT_APPROVED"
    (wf / "autopilot.json").write_text('{"kill": false}', encoding="utf-8")
    r = o.start_session("claude", "alpha", "x", by="autopilot", model=M_SON, env={"JARVES_WORKFLOW_AGENT": "ap-bugs-1"})
    assert r["ok"], r
    # the unconfirmed-start lookup finds exactly that worker's live session
    assert o.call("find_worker", ref="ap-bugs-1", by="autopilot")["session_id"] == r["session"]["id"]
    assert o.call("find_worker", ref="ap-other", by="autopilot")["session_id"] == ""
    o.stop_session(r["session"]["id"], by="autopilot")


def test_autopilot_notify_op_reaches_the_owner_channel(world):
    o = world["o"]
    got = []
    old, o.notify = o.notify, None
    assert o.call("notify", text="x", kind="autopilot_alert", by="autopilot")["ok"] is False
    o.notify = lambda en, he, kind: got.append((en, kind))
    assert o.call("notify", text="idle with work", kind="autopilot_alert", by="autopilot")["ok"] is True
    assert got == [("idle with work", "autopilot_alert")]
    o.notify = old


def test_autopilot_worker_default_list_matches_owner_rights_and_never_a_bypass(world):
    """P1: a headless worker could not run one test (popup timed out while the owner slept). Same working rights, no bypass."""
    from jarves.orchestrator import claude
    rules = claude.autopilot_allowed_tools("C:\py\python.exe")
    assert "Bash(python:*)" in rules and "Bash(gh pr:*)" in rules and "Bash(git push jarves:*)" not in rules and "Bash(jarvis:*)" not in rules
    assert "Bash(git push*)" in claude.WORKER_DENY and "Bash(jarvis hotfix*)" in claude.WORKER_DENY
    for hard_stop in ("Bash(git push:*)", "Bash(git tag:*)", "Bash(rm:*)", "Bash(gh release:*)", "Bash(powershell:*)",
                      "Bash(schtasks:*)", "Bash(taskkill:*)"):
        assert hard_stop not in rules
    assert claude.WORKER_PERMISSION_MODE == "acceptEdits"
    assert not any("dangerously" in x.lower() or "bypass" in x.lower() for x in rules)
    m = claude.ManagedClaude(["claude"], ".", plugin_dir=None, on_event=lambda k, d: None, allowed_tools=rules,
                             permission_mode="acceptEdits")
    assert m.argv[m.argv.index("--permission-mode") + 1] == "acceptEdits"
    assert not any("dangerously" in a or "bypass" in a.lower() for a in m.argv)
    bad = claude.ManagedClaude(["claude"], ".", plugin_dir=None, on_event=lambda k, d: None, permission_mode="bypassPermissions")
    assert bad.argv[bad.argv.index("--permission-mode") + 1] == "default"
    i = m.argv.index("--allowedTools")
    assert m.argv[i + 1:i + 1 + len(rules)] == rules
    assert "--allowedTools" not in claude.ManagedClaude(["claude"], ".", plugin_dir=None, on_event=lambda k, d: None).argv


def test_autopilot_worker_start_uses_isolated_worktree_mode_and_policy_override(world):
    from jarves.workflow import autopilot
    o, owner, home = world["o"], world["owner"], world["home"]
    assert _grant(o, owner, max_concurrent=3)["ok"]
    owner.policy = "approve"
    r = o.start_session("claude", "alpha", "x", by="autopilot", model=M_SON)
    argv = o.managed[r["session"]["id"]].argv
    assert r["ok"] and "--allowedTools" in argv and "Bash(git push:*)" not in argv
    r2 = o.start_session("claude", "alpha", "x", by="owner")
    assert r2.get("ok"), r2
    a2 = o.managed[r2["session"]["id"]].argv
    assert "--allowedTools" not in a2 and a2[a2.index("--permission-mode") + 1] == "default"
    policy, _ = autopilot.load(home)
    policy["worker_allowed_tools"] = ["Bash(ls:*)"]
    autopilot.save(home, policy)
    r3 = o.start_session("claude", "alpha", "x", by="autopilot", model=M_SON)
    a3 = o.managed[r3["session"]["id"]].argv
    assert a3[a3.index("--allowedTools") + 1] == "Bash(ls:*)"


def test_policy_worker_allowed_tools_is_validated(tmp_path):
    from jarves.workflow import autopilot
    base, _ = autopilot.load(tmp_path)
    assert "worker_allowed_tools" not in base
    autopilot.validate({**base, "worker_allowed_tools": ["Bash(ls:*)", "Read"]})
    for bad in ([], "Bash(ls:*)", ["ls"], ["Bash(" + "x" * 80 + ")"], ["Bash(ls:*)", "Bash(ls:*)"], [1],
                ["Bash(claude --dangerously-skip-permissions:*)"], ["Bash(a)"] * 41):
        with pytest.raises(ValueError):
            autopilot.validate({**base, "worker_allowed_tools": bad})
    autopilot.set_key(tmp_path, "worker_allowed_tools", '["Bash(ls:*)"]')
    assert autopilot.load(tmp_path)[0]["worker_allowed_tools"] == ["Bash(ls:*)"]
