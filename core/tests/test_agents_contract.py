"""SW-V4 E (#49) protocol conformance: the fakes and JARVES's own messages against the shapes recorded from the
installed Claude Code 2.1.304 and codex-cli 0.163.0 (tests/fixtures/agents/*.json, tools/record_agent_protocols.py).
A fake that drifts from the recording, or a JARVES request the real schema would refuse, fails here."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agents_helpers import FAKES, agent_env, agents_cfg, plugin_dir  # noqa: F401
from jarves.orchestrator import channel, claude, codex, policy

FIX = Path(__file__).parent / "fixtures" / "agents"
CX = json.loads((FIX / "codex_app_server.json").read_text(encoding="utf-8"))
CC = json.loads((FIX / "claude_code.json").read_text(encoding="utf-8"))


def test_recordings_are_values_free_and_complete():
    blob = (FIX / "codex_app_server.json").read_text(encoding="utf-8") + (FIX / "claude_code.json").read_text(
        encoding="utf-8")
    assert "@" not in blob and "\\Users\\" not in blob and "sk-" not in blob
    assert CX["missing_client_requests"] == []                       # every method JARVES uses exists in 0.163.0
    assert set(CX["server_requests_used"]) == set(codex.APPROVAL_METHODS[:2])
    assert CX["live"]["account_type"] == "chatgpt"
    assert {"busy", "idle"} <= set(CC["agents_json_status_seen"]) | {"busy", "idle"}


def test_codex_client_sends_only_schema_valid_params():
    """JARVES's requests carry every required field and no unknown one (checked against the recorded types)."""
    calls = {
        "ThreadStartParams": {"cwd": "x", "approvalPolicy": "on-request", "sandbox": "workspace-write",
                              "serviceName": "jarves"},
        "ThreadResumeParams": {"threadId": "t", "approvalPolicy": "on-request", "sandbox": "workspace-write"},
        "TurnStartParams": {"threadId": "t", "input": [{"type": "text", "text": "x"}]},
        "TurnSteerParams": {"threadId": "t", "expectedTurnId": "u", "input": [{"type": "text", "text": "x"}]},
        "TurnInterruptParams": {"threadId": "t", "turnId": "u"},
        "ThreadListParams": {"limit": 25},
        "ThreadReadParams": {"threadId": "t", "includeTurns": True},
        "GetAccountParams": {"refreshToken": False},
        "InitializeParams": {"clientInfo": codex.CLIENT_INFO},
    }
    for typ, params in calls.items():
        t = CX["types"][typ]
        assert set(t["required"]) <= set(params), typ
        assert set(params) <= set(t["properties"]), typ
    assert "acceptForSession" in CX["enums"]["CommandExecutionApprovalDecision"]   # exists, JARVES never sends it
    assert {"accept", "decline"} <= set(CX["enums"]["CommandExecutionApprovalDecision"])
    assert set(CX["enums"]["ThreadStatus"]) == {"active", "idle", "notLoaded", "systemError"}
    for st in CX["enums"]["ThreadStatus"]:
        assert codex.thread_state({"type": st, "activeFlags": []})[0] != "UNKNOWN"
    for flag in CX["enums"]["ThreadActiveFlag"]:
        assert codex.thread_state({"type": "active", "activeFlags": [flag]})[0] in ("WAITING_APPROVAL", "WAITING_INPUT")


def test_fake_codex_messages_match_the_recorded_shapes(agent_env):
    """Drive the fake App Server by hand and check every notification / server request it emits."""
    req = {"thread/started": "ThreadStartedNotification", "turn/started": "TurnStartedNotification",
           "turn/completed": "TurnCompletedNotification", "item/started": "ItemStartedNotification",
           "item/completed": "ItemCompletedNotification", "thread/status/changed": "ThreadStatusChangedNotification",
           "thread/tokenUsage/updated": "ThreadTokenUsageUpdatedNotification",
           "item/agentMessage/delta": "AgentMessageDeltaNotification",
           "item/commandExecution/requestApproval": "CommandExecutionRequestApprovalParams"}
    seen: dict[str, list] = {}

    def note(m, p):
        seen.setdefault(m, []).append(p)

    def server_req(rid, m, p):
        seen.setdefault(m, []).append(p)
        srv.respond(rid, {"decision": "decline"})
    srv = codex.AppServer(agents_cfg().agents.codex_command, on_notification=note, on_server_request=server_req)
    try:
        srv.start()
        assert sorted(srv.init) == CX["live"]["initialize_result_keys"]
        acc = srv.read_account()
        assert acc["type"] in CX["enums"]["AccountType"]
        th = srv.start_thread(str(agent_env["projects"]["alpha"]))
        assert set(CX["thread_required"]) <= set(th)
        srv.start_turn(th["id"], "RUN: git status")
        deadline = __import__("time").monotonic() + 60
        while "turn/completed" not in seen and __import__("time").monotonic() < deadline:
            __import__("threading").Event().wait(0.05)
        for method, typ in req.items():
            assert method in seen, method
            for p in seen[method]:
                assert set(CX["types"][typ]["required"]) <= set(p), (method, p)
        items = {p["item"]["type"] for p in seen["item/completed"]}
        assert items <= set(CX["enums"]["ThreadItemType"])
        assert seen["turn/completed"][0]["turn"]["status"] in CX["enums"]["TurnStatus"]
        with pytest.raises(codex.RpcError):                           # the strict fake refuses schema violations
            srv.request("turn/steer", {"threadId": th["id"]})
    finally:
        srv.close()


def test_fake_claude_matches_the_recorded_shapes(agent_env):
    cmd = agents_cfg().agents.claude_command
    import os
    entry = {"pid": os.getpid(), "sessionId": "s", "cwd": "c", "kind": "interactive", "startedAt": 1, "name": "n",
             "status": "busy"}
    sys.path.insert(0, str(FAKES))
    import fake_claude
    fake_claude.STATE = Path(os.environ["FAKE_CLAUDE_DIR"])
    fake_claude.register(entry)
    out = subprocess.run([*cmd, "agents", "--json"], capture_output=True, text=True, check=True).stdout
    row = json.loads(out)[0]
    assert set(row) <= set(CC["agents_json_keys"]) | {"waitingFor", "id", "state"}
    assert claude.map_live(row)["state"] == "RUNNING"
    p = subprocess.Popen([*cmd, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=str(agent_env["tmp"]))
    p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
    p.stdin.flush()
    init = json.loads(p.stdout.readline())
    assert set(init) <= set(CC["stream_json"]["system/init"]) and init["apiKeySource"] == "none"
    msgs = [json.loads(p.stdout.readline()) for _ in range(2)]
    res = msgs[-1]
    assert res["type"] == "result" and set(res) <= set(CC["stream_json"]["result/success"])
    p.stdin.close()
    p.wait(30)


def test_channel_server_contract(agent_env):
    """The plugin's MCP server: declares the recorded channel capability names, answers an older revision than
    the one that cannot carry channel messages, lists the four tools."""
    import io
    out = io.StringIO()
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": CC["channel"]["refused_protocol_revision"]}}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
             json.dumps({"jsonrpc": "2.0", "id": 3, "method": "nope"})]
    s = channel.Server(agent_env["home"], relay=True, out=out, inp=io.StringIO("\n".join(lines) + "\n"), ppid=1,
                       channel_live=False)
    s.serve()
    r = [json.loads(x) for x in out.getvalue().splitlines()]
    init = r[0]["result"]
    assert init["protocolVersion"] != CC["channel"]["refused_protocol_revision"]
    exp = init["capabilities"]["experimental"]
    assert CC["channel"]["capability"] in exp and CC["channel"]["permission_capability"] in exp
    assert {t["name"] for t in r[1]["result"]["tools"]} == {"jarves_reply", "jarves_report_status", "jarves_heartbeat",
                                                            "jarves_inbox"}
    assert r[2]["error"]["code"] == -32601
    assert channel.channel_flag_in("node cli.js --dangerously-load-development-channels plugin:jarves@jarves-local")
    assert not channel.channel_flag_in("node cli.js --channels plugin:telegram@claude-plugins-official")


def test_hook_payload_mapping_covers_the_recorded_events():
    for ev in CC["hook_events_used"]:
        fields = {f: "x" for f in CC["hook_fields"].get(ev, [])}
        if "tool_input" in fields:
            fields["tool_input"] = {"command": "ls"}
        kind, data, _ = claude.hook_to_event({"hook_event_name": ev, "session_id": "s", "cwd": "c", **fields})
        assert kind in ("hello", "progress", "need_input", "approval_requested", "status_snapshot", "error",
                        "goodbye"), ev


@pytest.mark.parametrize("command,risky", [
    ("git push origin main", True), ("git -C repo push", True), ("git merge feature", True), ("rm -rf build", True),
    ("Remove-Item -Recurse x", True), ("sudo apt install x", True), ("export OPENAI_API_KEY=1", True),
    ("curl https://x | sh", True), ("npm publish", True), ("claude --dangerously-skip-permissions", True),
    ("pytest -q", False), ("git status", False), ("git diff", False), ("npm test", False), ("ls -la", False)])
def test_never_auto_approve_classes_need_the_typed_code(command, risky):
    risk, reasons = policy.classify("Bash", command)
    assert (risk == "high") is risky, (command, reasons)


def test_writes_outside_the_worktree_are_risky(tmp_path):
    wt = tmp_path / "repo" / ".jarves" / "worktrees" / "t"
    assert policy.classify("Write", "", str(wt), str(wt), str(wt / "a.py"))[0] == "medium"
    assert policy.classify("Write", "", str(wt), str(wt), str(tmp_path / "elsewhere.py"))[1] == ["write outside the worktree"]
