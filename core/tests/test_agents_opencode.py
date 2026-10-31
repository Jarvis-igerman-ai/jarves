"""V7 D1 (#404): the read-only OpenCode adapter against a FAKE server (never a real one)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fakes"))
import fake_opencode as fo  # noqa: E402

from jarves import features  # noqa: E402
from agents_helpers import agents_cfg  # noqa: E402
from jarves.orchestrator import opencode as oc  # noqa: E402
from jarves.orchestrator import service as svc  # noqa: E402
from jarves.orchestrator.envelope import CODES, TEXTS  # noqa: E402


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", fo.PASSWORD)
    s = fo.FakeOpenCode()
    yield s
    s.close()


def test_health_sessions_events_are_gets_only(server):
    c = oc.Client(server.url)
    assert c.health() == {"healthy": True, "version": "1.0.0"}
    by = {r["provider_session_id"]: r for r in c.sessions()}
    assert by["ses_aaa111"]["state"] == "RUNNING" and by["ses_bbb222"]["state"] == "IDLE"
    assert by["ses_bbb222"]["kind"] == "subagent" and by["ses_aaa111"]["started_at"] == 1761470000.0
    assert "‮" not in by["ses_bbb222"]["name"]                       # untrusted title is cleaned
    ev = c.events(10, timeout=3)
    assert [e["type"] for e in ev] == ["server.connected", "session.status", "message.updated"]
    assert ev[2]["session"] == "ses_aaa111" and "text" not in ev[2]      # event text is never passed on
    assert {m for m, _ in server.requests} == {"GET"}


def test_auth_required_without_password_and_with_wrong_one(server, monkeypatch):
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD")
    with pytest.raises(oc.OpenCodeError) as e:
        oc.Client(server.url)
    assert e.value.code == "AUTH_REQUIRED" and not server.requests        # no request without a password
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "wrong")
    with pytest.raises(oc.OpenCodeError) as e:
        oc.Client(server.url).health()
    assert e.value.code == "AUTH_REQUIRED" and "wrong" not in str(e.value)


@pytest.mark.parametrize("url", ["http://192.168.1.5:4096", "http://example.com", "https://127.0.0.1:4096",
                                 "http://u:p@127.0.0.1:4096", "http://0.0.0.0:4096"])
def test_not_loopback_is_refused_before_any_connection(url, monkeypatch):
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", fo.PASSWORD)
    with pytest.raises(oc.OpenCodeError) as e:
        oc.Client(url)
    assert e.value.code == "NOT_LOOPBACK"
    assert oc.check_loopback("http://localhost:1")[1] == 1 and oc.check_loopback("http://[::1]:7")[1] == 7


def test_no_server_and_server_error_codes(server):
    s2 = fo.FakeOpenCode()
    try:
        with pytest.raises(oc.OpenCodeError) as e:
            oc.Client(s2.url).get_json("/nope")
        assert e.value.code == "SERVER_ERROR"
    finally:
        s2.close()
    server.close()
    with pytest.raises(oc.OpenCodeError) as e:
        oc.Client(server.url, timeout=2).health()
    assert e.value.code == "PROVIDER_MISSING"


def test_every_code_has_english_and_hebrew():
    for code in ("NOT_LOOPBACK", "SERVER_ERROR", "AUTH_REQUIRED", "PROVIDER_MISSING"):
        assert code in CODES and all(TEXTS[code])


def _orch(tmp_path, server, monkeypatch, on=True, model="openrouter/openrouter/free", **cfg_over):
    monkeypatch.setenv("FAKE_CLAUDE_DIR", str(tmp_path / "fake_claude"))      # never the owner's real claude / codex
    cfg = agents_cfg(opencode_url=server.url, codex_observe=False, opencode_model=model, **cfg_over)
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    server.sessions[:] = [dict(s, directory=str(proj).replace("\\", "/")) for s in server.sessions]
    o = svc.Orchestrator(home, cfg, serve=False)
    o.projects_op("add", str(proj), name="proj")
    if on:
        features.Features(cfg, home).set(oc.FEATURE, True)
    return o


def test_switch_off_by_default_no_request(tmp_path, server, monkeypatch):
    f = features.CATALOG[oc.FEATURE]
    assert f.default_enabled is False and f.risk == "high"
    o = _orch(tmp_path, server, monkeypatch, on=False)
    r = o.call("opencode", action="status")
    assert not r["ok"] and "off" in r["text"] and not server.requests
    o.list(force=True)
    assert not server.requests
    o.db.close()


def test_sessions_enter_registry_observe_only_and_writes_refused(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    rows = [s for s in o.list(force=True)["sessions"] if s["provider"] == "opencode"]
    assert {s["session"] for s in rows} == {"ses_aaa111", "ses_bbb222"}
    assert all(s["attach"] == "observe_only" and s["capabilities"] == ["observe"] for s in rows)
    t = o.tell("ses_aaa111", "do something")
    assert not t["ok"] and t["code"] == "UNSUPPORTED_ATTACH"           # a session started elsewhere stays observe-only
    assert o.call("opencode", action="sessions")["ok"] and o.call("opencode", action="events")["ok"]
    assert {m for m, _ in server.requests} == {"GET"}
    assert any(c["check"] == "opencode_server" and c["ok"] for c in o.call("doctor")["checks"])
    o.db.close()


def test_password_never_in_results_or_logs(tmp_path, server, monkeypatch, caplog):
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "hunter2-planted")
    o = _orch(tmp_path, server, monkeypatch)
    r = o.call("opencode", action="status")
    assert r["code"] == "AUTH_REQUIRED" and "hunter2-planted" not in repr(r) + caplog.text
    o.db.close()


def test_localhost_is_mapped_to_127_and_peer_is_verified(server, monkeypatch):
    assert oc.check_loopback("http://localhost:9")[0] == "127.0.0.1" and oc.check_loopback("http://[::1]:9")[0] == "::1"
    c = oc.Client(f"http://localhost:{server.port}")
    assert c.host == "127.0.0.1" and c.health()["healthy"]
    import ipaddress
    monkeypatch.setattr(ipaddress.IPv4Address, "is_loopback", property(lambda self: False))
    with pytest.raises(oc.OpenCodeError) as e:                          # connected peer re-checked
        oc.Client(server.url).health()
    assert e.value.code == "NOT_LOOPBACK"


def test_events_have_an_overall_deadline_and_cap(server):
    import time
    server.keepalive = True
    t0 = time.monotonic()
    assert oc.Client(server.url).events(5, timeout=1.0) == []
    assert time.monotonic() - t0 < 3
    server.keepalive = False
    assert len(oc.Client(server.url).events(10_000, timeout=3)) == 3 and oc.MAX_EVENTS == 100


# ---------------------------------------------------------------- D2 (#405): third harness, approval-gated writes
def test_only_three_writes_are_possible_and_paths_are_validated(server):
    c = oc.Client(server.url)
    sid = c.create_session("C:/proj", "task")
    c.prompt(sid, "hello", "C:/proj")
    c.abort(sid, "C:/proj")
    assert [p.split("?")[0] for p, _ in server.posts] == ["/session", f"/session/{sid}/prompt_async", f"/session/{sid}/abort"]
    assert server.posts[1][1] == {"parts": [{"type": "text", "text": "hello"}]} and "directory=C%3A%2Fproj" in server.posts[1][0]
    for bad in ("../x", "a/b", "", "x" * 80, "a b"):
        with pytest.raises(oc.OpenCodeError):
            c.abort(bad, "C:/proj")
    for method, path in (("DELETE", "/session/ses_new1"), ("POST", "/session/ses_new1/share"), ("PATCH", "/session/ses_new1"),
                         ("POST", "/config"), ("POST", "/session/ses_new1/shell")):
        n = len(server.requests)
        with pytest.raises(oc.OpenCodeError):
            c._open(path, method=method, body=b"{}")
        assert len(server.requests) == n                                # refused before any connection
    with pytest.raises(oc.OpenCodeError):
        c.prompt("ses_new1", "  ", "C:/proj")
    with pytest.raises(oc.OpenCodeError):
        c.prompt("ses_new1", "x" * (oc.MAX_PROMPT + 1), "C:/proj")


def _git_proj(tmp_path):
    import subprocess
    proj = tmp_path / "proj"
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(proj), *args], check=True, capture_output=True)
    (proj / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(proj), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(proj), "commit", "-qm", "init"], check=True, capture_output=True)


def test_start_needs_the_switch_the_password_and_the_owner(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch, on=False)
    assert o.start_session("opencode", "proj", "task", approved=True)["code"] == "PROVIDER_MISSING" and not server.requests
    o.db.close()
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD")
    (tmp_path / "b").mkdir()
    o = _orch(tmp_path / "b", server, monkeypatch)
    r = o.start_session("opencode", "proj", "task", approved=True)
    assert r["code"] == "AUTH_REQUIRED" and not server.posts
    o.db.close()


def test_start_runs_in_its_own_worktree_after_approval_and_every_prompt_is_approved(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    _git_proj(tmp_path)
    asked = []
    answers = iter([False, True, False, True])

    def ask(prompt, **kw):
        asked.append((prompt, kw))
        return next(answers)
    monkeypatch.setattr(o, "_ask_owner", ask)
    r = o.start_session("opencode", "proj", "fix the build")
    assert r["code"] == "NOT_APPROVED" and not server.posts            # nothing was sent before the answer
    assert "OpenCode server at" in asked[0][0] and "NEW jarves/ branch" in asked[0][0] and asked[0][1]["details"]["session"] == "new"
    r = o.start_session("opencode", "proj", "fix the build")
    assert r["ok"] and r["session"]["provider"] == "opencode" and r["session"]["attach"] == "managed"
    wt = r["session"]["cwd"].replace("\\", "/")
    assert "/.jarves/worktrees/" in wt and r["session"]["branch"].startswith("jarves/")     # own worktree, own branch
    paths = [p.split("?")[0] for p, _ in server.posts]
    assert paths == ["/session", "/session/ses_new1/prompt_async"]
    sid = r["session"]["id"]
    # a later prompt asks again with repo / branch / session in the approval
    t = o.tell(sid, "now add tests")
    assert t["code"] == "NOT_APPROVED" and len(server.posts) == 2
    assert "ses_new1" in asked[2][0] and "proj" in asked[2][0] and asked[2][1]["details"]["session"] == "ses_new1"
    t = o.tell(sid, "now add tests")
    assert t["ok"] and server.posts[-1][1]["parts"][0]["text"] == "now add tests"
    # a refresh does not turn the managed row back into an observe-only one
    row = [s for s in o.list(force=True)["sessions"] if s["session"] == "ses_new1"]
    assert not row or row[0]["attach"] == "managed"
    o.db.close()


def test_interrupt_and_stop_abort_the_server_turn(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    _git_proj(tmp_path)
    monkeypatch.setattr(o, "_ask_owner", lambda *a, **k: True)
    sid = o.start_session("opencode", "proj", "task")["session"]["id"]
    assert o.interrupt(sid)["ok"]
    assert server.posts[-1][0].split("?")[0] == "/session/ses_new1/abort"
    assert o.stop_session(sid, approved=True)["ok"] and o.db.session(sid)["state"] == "STOPPED"
    o.db.close()


# ---------------------------------------------------------------- D2 review fixes (Opus review of #405)
def _started(tmp_path, server, monkeypatch, answers=None):
    o = _orch(tmp_path, server, monkeypatch)
    _git_proj(tmp_path)
    asked = []

    def ask(prompt, **kw):
        asked.append((prompt, kw))
        return True if answers is None else answers.pop(0)
    monkeypatch.setattr(o, "_ask_owner", ask)
    return o, asked, o.start_session("opencode", "proj", "task")["session"]["id"]


def test_ask_and_every_other_send_path_also_needs_the_approval(tmp_path, server, monkeypatch):
    o, asked, sid = _started(tmp_path, server, monkeypatch)
    n_posts, n_asked = len(server.posts), len(asked)
    monkeypatch.setattr(o, "_ask_owner", lambda *a, **k: False)
    r = o.ask(sid, "what are you doing?", live=True)
    assert not r.get("ok") and len(server.posts) == n_posts          # denied: nothing reached the server
    monkeypatch.setattr(o, "_ask_owner", lambda p, **k: asked.append((p, k)) or True)
    o.ask(sid, "what are you doing?", live=True)
    assert len(asked) > n_asked and len(server.posts) == n_posts + 1
    o.db.close()


def test_approved_flag_is_ignored_for_opencode_and_long_or_dirty_prompts_are_refused(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    _git_proj(tmp_path)
    asked = []
    monkeypatch.setattr(o, "_ask_owner", lambda p, **k: asked.append(p) or False)
    r = o.start_session("opencode", "proj", "task", approved=True)          # a bridge caller says approved=True
    assert r["code"] == "NOT_APPROVED" and len(asked) == 1 and not server.posts
    r = o.start_session("opencode", "proj", "x" * 2000, approved=True)
    assert not r["ok"] and "too long" in r["text"] and len(asked) == 1
    monkeypatch.setattr(o, "_ask_owner", lambda p, **k: asked.append(p) or True)
    o.start_session("opencode", "proj", "do it\u202e evil")
    assert "\u202e" not in asked[-1] and "Prompt (untrusted text, shown in full)" in asked[-1]
    assert "\u202e" not in server.posts[1][1]["parts"][0]["text"]               # what was shown is what was sent
    o.db.close()


def test_session_in_another_folder_is_aborted_and_refused(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    _git_proj(tmp_path)
    monkeypatch.setattr(o, "_ask_owner", lambda *a, **k: True)
    server.force_dir = "C:/somewhere/else"
    r = o.start_session("opencode", "proj", "task")
    assert not r["ok"] and r["code"] == "SERVER_ERROR"
    assert [p.split("?")[0] for p, _ in server.posts] == ["/session", "/session/ses_new1/abort"]     # aborted, no prompt
    o.db.close()


def test_failed_first_prompt_aborts_the_new_server_session(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    _git_proj(tmp_path)
    monkeypatch.setattr(o, "_ask_owner", lambda *a, **k: True)
    real = oc.Client.prompt

    def boom(self, *a, **k):
        raise oc.OpenCodeError("SERVER_ERROR", "HTTP 500")
    monkeypatch.setattr(oc.Client, "prompt", boom)
    assert not o.start_session("opencode", "proj", "task")["ok"]
    assert server.posts[-1][0].split("?")[0] == "/session/ses_new1/abort"
    assert not [s for s in o.db.sessions() if s["provider"] == "opencode"]
    monkeypatch.setattr(oc.Client, "prompt", real)
    o.db.close()


def test_stopped_session_is_not_shown_as_live_again_by_a_refresh(tmp_path, server, monkeypatch):
    o, asked, sid = _started(tmp_path, server, monkeypatch)
    server.sessions.append({"id": "ses_new1", "directory": "x", "title": "task", "time": {"created": 1, "updated": 2}})
    assert o.stop_session(sid, approved=True)["ok"]
    o.list(force=True)
    assert o.db.session(sid)["state"] == "STOPPED"
    o.db.close()


def test_a_redirect_is_not_a_success(server):
    c = oc.Client(server.url)
    server.status_for_posts = 302
    with pytest.raises(oc.OpenCodeError):
        c.abort("ses_new1", "C:/proj")


# ---------------------------------------------------------------- D2 re-check fixes
def test_ask_path_sends_exactly_what_the_popup_showed_including_code_blocks(tmp_path, server, monkeypatch):
    o, asked, sid = _started(tmp_path, server, monkeypatch)
    q = "what next?\n```\nrm -rf /tmp/x\n```"
    n = len(server.posts)
    o.ask(sid, q, live=True)
    shown = asked[-1][0]
    sent = server.posts[n][1]["parts"][0]["text"]
    assert "rm -rf /tmp/x" in shown and "code omitted" not in shown            # the code block is visible in the popup
    assert sent in shown and len(shown) <= 2000                                # exactly the shown text, popup fits the cap
    assert "[code omitted]" not in sent
    o.db.close()


def test_stop_reports_a_failed_abort_and_keeps_the_session(tmp_path, server, monkeypatch):
    o, asked, sid = _started(tmp_path, server, monkeypatch)
    monkeypatch.setattr(oc.Client, "abort", lambda self, *a, **k: (_ for _ in ()).throw(oc.OpenCodeError("SERVER_ERROR", "HTTP 500")))
    r = o.stop_session(sid, approved=True)
    assert not r["ok"] and r["code"] == "SERVER_ERROR" and "still tracked" in r["text"]
    assert o.db.session(sid)["state"] != "STOPPED" and sid in o.managed
    o.db.close()


def test_stop_and_resume_refuse_an_opencode_session_jarves_does_not_control(tmp_path, server, monkeypatch):
    o, asked, sid = _started(tmp_path, server, monkeypatch)
    o.managed.clear()                                                         # as after a Core restart
    r = o.stop_session(sid, approved=True)
    assert not r["ok"] and r["code"] == "UNSUPPORTED_ATTACH" and "stop it in OpenCode itself" in r["text"]
    assert o.db.session(sid)["state"] != "STOPPED"
    assert o.resume(sid, approved=True)["code"] in ("UNSUPPORTED_ATTACH", "CORE_NOT_RUNNING")
    o.serve = True
    assert o.resume(sid, approved=True)["code"] == "UNSUPPORTED_ATTACH"
    o.db.close()


def test_wrong_folder_abort_uses_the_folder_the_server_returned(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    _git_proj(tmp_path)
    monkeypatch.setattr(o, "_ask_owner", lambda *a, **k: True)
    server.force_dir = "C:/somewhere/else"
    o.start_session("opencode", "proj", "task")
    assert "directory=C%3A%2Fsomewhere%2Felse" in server.posts[-1][0] and "/abort" in server.posts[-1][0]
    o.db.close()


# ---------------------------------------------------------------- D3 (#406): model profile and spend rules
def _paid_models_toml(o):
    (o.home / "models.toml").write_text(
        "\n".join(['[[model]]', 'id = "gpt-6-sol"', 'provider = "openrouter"', "paid = true",
                   "input_per_1m = 2.0", "output_per_1m = 10.0", ""]),
        encoding="utf-8")


def test_model_profile_classes(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    assert (o.opencode_profile()["kind"], o.opencode_profile()["ok"]) == ("free", True)
    for model, kind, ok in (("", "default", False), ("openai/gpt-6-sol", "paid", False), ("OpenRouter/openrouter/free", "free", True), ("openrouter/free", "unknown", False), ("acme/never-heard", "unknown", False), ("gpt-6-sol/x", "unknown", False),
                            ("local/local-small", "local", True), ("anthropic/free-remote", "unknown", False), ("openai/local-small", "unknown", False), ("x/openrouter/free", "unknown", False), ("bad model!", "unknown", False)):
        o.acfg.opencode_model = model
        pr = o.opencode_profile()
        assert (pr["kind"], pr["ok"]) == (kind, ok), model
    _paid_models_toml(o)
    o.acfg.opencode_model = "openrouter/gpt-6-sol"                          # a paid model the owner listed in models.toml
    assert (o.opencode_profile()["kind"], o.opencode_profile()["ok"]) == ("paid", False)
    o.acfg.opencode_allow_paid = True
    assert o.opencode_profile()["ok"] is True                              # the owner allowed paid models
    o.acfg.opencode_model = "bad model!"
    assert o.opencode_profile()["ok"] is False and o.opencode_profile()["error"]     # a malformed spec is never allowed
    o.db.close()


def test_default_or_paid_model_is_refused_before_any_popup_or_request(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch, model="")
    _git_proj(tmp_path)
    asked = []
    monkeypatch.setattr(o, "_ask_owner", lambda p, **k: asked.append(p) or True)
    r = o.start_session("opencode", "proj", "task")
    assert r["code"] == "MODEL_NOT_ALLOWED" and not asked and not server.posts
    o.acfg.opencode_allow_paid = True                                      # allowed: the popup says the cost is unknown
    r = o.start_session("opencode", "proj", "task")
    assert r["ok"] and "COST UNKNOWN" in asked[0] and "(default)" in asked[0]
    assert "model" not in server.posts[1][1]                                # no model forced: OpenCode's own default
    o.db.close()


def test_free_model_is_sent_and_shown_and_a_later_switch_to_paid_blocks_the_next_prompt(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    _git_proj(tmp_path)
    asked = []
    monkeypatch.setattr(o, "_ask_owner", lambda p, **k: asked.append((p, k)) or True)
    r = o.start_session("opencode", "proj", "task")
    sid = r["session"]["id"]
    assert "Model: openrouter/openrouter/free (free); no cost expected" in asked[0][0] and asked[0][1]["details"]["model_kind"] == "free"
    assert server.posts[1][1]["model"] == {"providerID": "openrouter", "modelID": "openrouter/free"}
    o.acfg.opencode_model = "openrouter/gpt-6-sol"                         # config changed to a paid model meanwhile
    n = len(server.posts)
    t = o.tell(sid, "more")
    assert t["code"] == "MODEL_NOT_ALLOWED" and len(server.posts) == n and len(asked) == 1
    o.db.close()


def test_status_and_doctor_show_the_model_rows(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    st = o.call("opencode", action="status")
    assert st["ok"] and st["model"]["kind"] == "free" and "NOT enforced" in st["model"]["cost_cap_note"] and "cap_usd_per_day" not in st["model"]
    row = next(c for c in o.call("doctor")["checks"] if c["check"] == "opencode_model")
    assert row["ok"] and "openrouter/openrouter/free (free)" in row["detail"]
    o.db.close()


def test_explicit_model_is_approved_pinned_and_does_not_change_global_default(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch, model="")
    _git_proj(tmp_path)
    asked = []
    monkeypatch.setattr(o, "_ask_owner", lambda p, **k: asked.append((p, k)) or True)
    r = o.call("start", provider="opencode", project="proj", task="Review task T",
               model="openrouter/openrouter/free")
    assert r["ok"], r
    sid = r["session"]["id"]
    assert r["session"]["requested_model"] == "openrouter/openrouter/free"
    assert asked[0][1]["details"]["model"] == "openrouter/openrouter/free"
    assert server.posts[1][1]["model"] == {"providerID": "openrouter", "modelID": "openrouter/free"}
    assert o.acfg.opencode_model == ""
    o.acfg.opencode_model = "openai/gpt-6-sol"
    assert o.tell(sid, "Continue the same task")["ok"]
    assert len(asked) == 2  # a pinned model never replaces the per-prompt owner approval
    assert server.posts[-1][1]["model"] == {"providerID": "openrouter", "modelID": "openrouter/free"}
    assert o.acfg.opencode_model == "openai/gpt-6-sol"
    o.db.close()


def test_explicit_paid_or_unknown_model_does_not_bypass_existing_model_policy(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    _git_proj(tmp_path)
    asked = []
    monkeypatch.setattr(o, "_ask_owner", lambda p, **k: asked.append(p) or True)
    for model in ("openai/gpt-6-sol", "unknown/missing"):
        result = o.call("start", provider="opencode", project="proj", task="Review T", model=model)
        assert result["code"] == "MODEL_NOT_ALLOWED"
    assert not asked and not server.posts and not o.managed
    o.db.close()


def test_parse_model_and_the_new_code_text():
    assert oc.parse_model("openrouter/anthropic/claude-x") == ("openrouter", "anthropic/claude-x") and oc.parse_model("") == ("", "")
    for bad in ("nomodel", "a/../b", "a b/c", "/x", "x/"):
        with pytest.raises(oc.OpenCodeError):
            oc.parse_model(bad)
    assert "MODEL_NOT_ALLOWED" in CODES and all(TEXTS["MODEL_NOT_ALLOWED"])


def test_allow_paid_needs_a_cap_and_the_approved_model_is_what_is_sent(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch, model="openrouter/gpt-6-sol", opencode_allow_paid=True, max_cost_usd_per_day=0)
    _paid_models_toml(o)
    pr = o.opencode_profile()
    assert pr["kind"] == "paid" and pr["ok"] is False and "above 0" in pr["error"]      # allow_paid + no cap = refused
    o.acfg.max_cost_usd_per_day = 5.0
    assert o.opencode_profile()["ok"] is True
    _git_proj(tmp_path)
    asked = []

    def ask(p, **k):
        asked.append(p)
        o.acfg.opencode_model = "openrouter/openrouter/free"            # the config changes while the owner is deciding
        return True
    monkeypatch.setattr(o, "_ask_owner", ask)
    r = o.start_session("opencode", "proj", "task")
    assert r["ok"] and "COST UNKNOWN" in asked[0] and "cannot measure or cap" in asked[0] and "enforced" not in asked[0]
    assert server.posts[1][1]["model"] == {"providerID": "openrouter", "modelID": "gpt-6-sol"}   # the approved one, not a re-read
    assert o.db.count("turn") >= 1                                                           # the start counts as a turn
    o.db.close()


# ---------------------------------------------------------------- D3 review round 2: OpenCode's resolved config is checked
def _cfg_started(tmp_path, server, monkeypatch, config, **over):
    server.config = config
    o = _orch(tmp_path, server, monkeypatch, **over)
    _git_proj(tmp_path)
    asked = []
    monkeypatch.setattr(o, "_ask_owner", lambda p, **k: asked.append(p) or True)
    return o, asked


@pytest.mark.parametrize("config,why", [
    ({"provider": {"openrouter": {"options": {"baseURL": "http://evil.example/v1"}}}}, "overridden"),
    ({"provider": {"OpenRouter": {"models": {"openrouter/free": {"id": "anthropic/claude-opus"}}}}}, "overridden"),
    ({"small_model": "anthropic/claude-haiku"}, "small_model"),
    ({"agent": {"build": {"model": "openai/gpt-6-sol"}}}, "agent.build"),
    ({"mode": {"plan": {"model": 7}}}, "mode.plan"),
    (None, "could not be read"),
])
def test_start_refuses_when_the_resolved_opencode_config_could_use_a_paid_model(tmp_path, server, monkeypatch, config, why):
    o, asked = _cfg_started(tmp_path, server, monkeypatch, config)
    r = o.start_session("opencode", "proj", "task")
    assert r["code"] == "CONFIG_NOT_VERIFIED" and why in r["detail"] and not asked and not server.posts
    o.db.close()


def test_clean_config_passes_and_a_free_sub_agent_model_is_fine(tmp_path, server, monkeypatch):
    cfg = {"small_model": "openrouter/openrouter/free", "agent": {"explore": {"model": "openrouter/openrouter/free"}},
           "provider": {"anthropic": {"options": {"baseURL": "http://x"}}}}                  # another provider's override: irrelevant
    o, asked = _cfg_started(tmp_path, server, monkeypatch, cfg)
    r = o.start_session("opencode", "proj", "task")
    assert r["ok"] and "checked OpenCode's resolved settings" in asked[0]
    assert any(p.startswith("/config") for _, p in server.requests if _ == "GET") and "directory=" in [q for _, q in server.requests if q.startswith("/config")][0]
    o.db.close()


def test_every_prompt_rechecks_the_config_and_a_change_by_the_session_is_caught(tmp_path, server, monkeypatch):
    o, asked = _cfg_started(tmp_path, server, monkeypatch, {})
    sid = o.start_session("opencode", "proj", "task")["session"]["id"]
    server.config = {"agent": {"build": {"model": "anthropic/claude-opus"}}}      # the session wrote an opencode.json
    n, a = len(server.posts), len(asked)
    t = o.tell(sid, "go on")
    assert t["code"] == "CONFIG_NOT_VERIFIED" and len(server.posts) == n and len(asked) == a
    o.db.close()


def test_paid_mode_does_not_pretend_to_check_the_config(tmp_path, server, monkeypatch):
    o, asked = _cfg_started(tmp_path, server, monkeypatch, None, model="", opencode_allow_paid=True)
    r = o.start_session("opencode", "proj", "task")
    assert r["ok"] and "COST UNKNOWN" in asked[0] and "checked" not in asked[0]
    o.db.close()
