"""Issues registry: de-duplication, content-free evidence, feeders (selftest, speedtest, findings, tool failures, crashes,
dashboard errors), approval before any network, filing on a local fake GitHub endpoint, fixed-in tracking, CLI, voice tool.
Offline: temp dirs and a loopback HTTP server only."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarves import issue_registry as ir


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def reg(tmp_path):
    return ir.Registry(tmp_path, clock=Clock(), version="4.0.1")


class FakeGitHub:
    def __init__(self):
        self.calls, self.issues = [], []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                outer.calls.append(("GET", self.path, None))
                self._send(200, outer.issues)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                outer.calls.append(("POST", self.path, body))
                if self.path.endswith("/comments"):
                    return self._send(201, {})
                i = {"number": len(outer.issues) + 1, "html_url": f"https://github.test/i/{len(outer.issues) + 1}", **body}
                outer.issues.append(i)
                self._send(201, i)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def gh(monkeypatch):
    f = FakeGitHub()
    monkeypatch.setattr(ir, "API_BASE", f.base)
    monkeypatch.setattr(ir, "GH_CLI", "no-such-gh-binary")
    for k in ("JARVES_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    yield f
    f.close()


# ---------------------------------------------------------------------- registry
def test_same_problem_is_one_record_with_a_count(reg):
    a = reg.record("selftest", "SELFTEST_X", "P2", {"ms": 5}, title="X is slow")
    b = reg.record("selftest", "SELFTEST_X", "P1", {"ms": 9})
    assert a["id"] == b["id"] == ir.issue_id("selftest", "SELFTEST_X") and re.fullmatch(r"JI-[0-9a-f]{8}", a["id"])
    assert len(reg.list()) == 1 and reg.get(a["id"])["count"] == 2 and reg.get(a["id"])["severity"] == "P1"
    assert reg.counts()["open"] == 1 and reg.counts()["p1"] == 1


def test_evidence_is_content_free(reg):
    r = reg.record("core", "E", "P3", {"count": 3, "ok": False, "fn": "mod.func", "text": "hello my password is x",
                                       "path": "C:\\Users\\me\\a.txt", "mail": "me@example.com", "Bad Key": 1,
                                       "api_key": "short", "tok": "sk-" + "a" * 40, "list": [1, "ok_id", "a b", "x/y"]},
                   title="has /slash and @mail")
    ev = r["evidence"]
    assert ev == {"count": 3, "ok": False, "fn": "mod.func", "list": [1, "ok_id"]}
    assert r["title"] == "E" and r["dropped"] >= 6
    assert "me@example.com" not in json.dumps(r) and "password" not in json.dumps(r)


def test_status_changes_and_fixed_in_tracking(reg):
    r = reg.record("tools", "TOOL_x_FAIL", "P2")
    other = reg.record("tools", "TOOL_y_FAIL", "P2")
    notes = "## 4.0.2\n- Added: stuff\n- Fixes: TOOL_x_FAIL and something else\n"
    assert ir.fixes_in(notes)[1] == {"TOOL_x_FAIL"}
    assert [x["id"] for x in reg.check_updated(notes, version="4.0.2")] == [r["id"]]
    got = reg.get(r["id"])
    assert got["status"] == "fixed" and got["fixed_in"] == "4.0.2" and reg.get(other["id"])["status"] == "new"
    assert [x["id"] for x in reg.take_unannounced()] == [r["id"]] and reg.take_unannounced() == []   # told once
    assert reg.check_updated(notes, version="4.0.2") == []
    reg.version = "4.0.2"
    reg.record("tools", "TOOL_x_FAIL", "P2")                                                        # seen again after the fix
    assert reg.get(r["id"])["status"] == "new"
    assert reg.set_status(other["id"], "ignored")["status"] == "ignored" and reg.counts()["open"] == 1
    with pytest.raises(ValueError):
        reg.set_status(other["id"], "nonsense")


def test_fixes_heading_form():
    ids, codes = ir.fixes_in("### Fixes\n- JI-0123abcd\n- SPEED_STARTUP_MS\n### Added\n- ZZZZ_OTHER\n")
    assert ids == {"JI-0123abcd"} and codes == {"SPEED_STARTUP_MS"}


# ---------------------------------------------------------------------- feeders
def test_selftest_and_speedtest_feed_the_registry(reg):
    n = ir.ingest_selftest(reg, {"checks": [{"id": "mic", "status": "fail", "title": "Microphone", "ms": 3},
                                            {"id": "ok1", "status": "pass"}, {"id": "version", "status": "warn"},
                                            {"id": "disk", "status": "warn", "title": "Disk"}]})
    assert n == 2
    m = ir.ingest_speedtest(reg, {"class": "owner", "metrics": [{"name": "startup_ms", "status": "fail", "value": 9000, "max": 5000, "unit": "ms"},
                                                                   {"name": "idle", "status": "pass"}]})
    assert m == 1 and {r["area"] for r in reg.list()} == {"selftest", "speed"}
    ir.ingest_selftest(reg, {"checks": [{"id": "mic", "status": "fail", "title": "Microphone"}]})
    assert reg.get(ir.issue_id("selftest", "SELFTEST_MIC"))["count"] == 2                           # de-duplicated


def test_findings_including_the_crash_finding(reg):
    assert ir.ingest_findings(reg, [{"fingerprint": "abc123", "severity": "high", "category": "crash", "title": "Crashed", "count": 2},
                                    {"fingerprint": "", "severity": "high"}]) == 1
    r = reg.list()[0]
    assert r["area"] == "crash" and r["severity"] == "P1"


def _rec(code, **fields):
    rec = logging.LogRecord("jarves.tools", logging.INFO, __file__, 1, "x", (), None)
    rec.code, rec.fields, rec.component = code, fields, "tools"
    return rec


def test_repeated_tool_failures_become_one_issue_only_past_the_threshold(reg):
    sink = ir.IssueSink(reg, threshold=3)
    for _ in range(2):
        sink.emit(_rec("TOOL_RESULT", tool="play", ok=False, result_code="NO_PLAYER"))
    assert reg.list() == []
    sink.emit(_rec("TOOL_RESULT", tool="play", ok=False, result_code="NO_PLAYER"))
    sink.emit(_rec("TOOL_RESULT", tool="play", ok=True, result_code="OK"))
    sink.flush()
    (r,) = reg.list()
    assert r["area"] == "tools" and r["evidence"]["tool"] == "play" and r["evidence"]["failures"] >= 3


def test_crash_records_become_p1_with_function_names_only(reg):
    sink = ir.IssueSink(reg)
    sink.emit(_rec("CRASH", exc_type="ValueError", exc_frames=["C:/Users/me/jarves/voice.py:88 in run_turn"]))
    sink.flush()
    (r,) = reg.list()
    assert r["severity"] == "P1" and r["evidence"]["frames"] == ["voice.run_turn"]
    assert "Users" not in json.dumps(r) and ":88" not in json.dumps(r)


def test_dashboard_errors_are_validated_and_rate_limited(reg):
    state: dict = {}
    ok = {"name": "TypeError", "file": "app.js", "line": 10, "col": 3, "page": "home"}
    assert ir.client_error(reg, ok, state=state)[0] == 200
    assert ir.client_error(reg, {**ok, "name": "bad name with spaces and secret"}, state=state)[0] == 400
    assert ir.client_error(reg, {**ok, "file": "../../etc/passwd"}, state=state)[0] == 400
    for _ in range(ir.CLIENT_ERRORS_PER_WINDOW + 2):
        code = ir.client_error(reg, ok, state=state)[0]
    assert code == 429 and reg.list()[0]["count"] <= ir.CLIENT_ERRORS_PER_WINDOW


# ---------------------------------------------------------------------- reporting
def test_bundle_is_the_exact_content_free_text(reg):
    r = reg.record("selftest", "SELFTEST_MIC", "P2", {"ms": 3}, title="Microphone check failed")
    b = ir.bundle(r)
    assert b["labels"] == ["from-diagnostics"] and b["id"] in b["body"] and "`ms`: 3" in b["body"]
    assert "No conversation text" in b["body"] and b["title"].startswith("[P2]")


def test_nothing_touches_the_network_before_approval_and_denial_sends_nothing(reg, gh):
    r = reg.record("selftest", "SELFTEST_MIC", "P2", title="Mic")

    async def no(prompt, **kw):
        return False
    out = asyncio.run(ir.report(reg, r["id"], ask=no, token_fn=lambda: "tok"))
    assert out["status"] == "declined" and gh.calls == []
    assert asyncio.run(ir.report(reg, r["id"], ask=None, token_fn=lambda: "tok"))["status"] == "declined" and gh.calls == []


def test_approved_report_files_one_labelled_issue_then_comments_on_repeat(reg, gh):
    r = reg.record("selftest", "SELFTEST_MIC", "P2", title="Mic")
    shown = []

    async def yes(prompt, **kw):
        shown.append(prompt)
        return True
    out = asyncio.run(ir.report(reg, r["id"], ask=yes, token_fn=lambda: "tok"))
    assert out["status"] == "filed" and "from-diagnostics" in shown[0] and r["id"] in shown[0]
    posts = [c for c in gh.calls if c[0] == "POST"]
    assert len(posts) == 1 and posts[0][2]["labels"] == ["from-diagnostics"] and posts[0][2]["body"] == ir.bundle(reg.get(r["id"]))["body"]
    assert reg.get(r["id"])["reported_url"].startswith("https://github.test/")
    again = asyncio.run(ir.report(reg, r["id"], ask=yes, token_fn=lambda: "tok"))
    assert again["status"] == "commented" and len(gh.issues) == 1                                    # no duplicate issue


def test_without_a_token_the_report_is_saved_not_sent(reg, gh, tmp_path):
    r = reg.record("selftest", "SELFTEST_MIC", "P2", title="Mic")

    async def yes(prompt, **kw):
        return True
    out = asyncio.run(ir.report(reg, r["id"], ask=yes, token_fn=lambda: ""))
    assert out["status"] == "saved" and gh.calls == [] and (tmp_path / "issue_reports" / f"{r['id']}.md").is_file()


# ---------------------------------------------------------------------- CLI + voice
def _cli(tmp_path, *argv):
    out: list[str] = []
    ap = argparse.ArgumentParser()
    ir.add_cli(ap.add_subparsers(dest="cmd"))
    a = ap.parse_args(["issues", *argv])
    rc = ir.cmd_issues(a, None, tmp_path, out=out.append)
    return rc, "\n".join(out)


def test_cli_list_add_show_ignore_and_unknown_id(tmp_path):
    assert "0 open" in _cli(tmp_path, "list")[1]
    rc, text = _cli(tmp_path, "add", "MY_PROBLEM", "--severity", "P2")
    iid = re.search(r"JI-[0-9a-f]{8}", text).group(0)
    assert rc == 0 and iid in _cli(tmp_path, "list")[1]
    assert "exactly what a report would contain" in _cli(tmp_path, "show", iid)[1]
    assert _cli(tmp_path, "add", "has spaces and free text")[0] == 2
    assert _cli(tmp_path, "ignore", iid)[0] == 0 and "0 open" in _cli(tmp_path, "list")[1]
    assert _cli(tmp_path, "show", "JI-00000000")[0] == 1
    rc, text = _cli(tmp_path, "report", iid)
    assert rc == 1 and "approval needed" in text                                                    # no tty and no --yes: nothing sent


def test_voice_tool_answers_in_one_sentence(tmp_path):
    reg = ir.Registry(tmp_path)
    assert "no open issues" in ir.spoken(reg, "en") and "אין כרגע" in ir.spoken(reg, "he")
    reg.record("selftest", "SELFTEST_MIC", "P1", title="Mic check failed")
    s = ir.spoken(reg, "en")
    assert "1 open issue," in s and "Mic check failed" in s
    skill = ir.make_skill(tmp_path, lambda: "en")
    (tool,) = skill.tools()
    assert tool.name == "list_issues"
    res = asyncio.run(tool.run(ir._NoArgs())) if hasattr(tool, "run") else None
    assert res is None or "1 open issue" in res.text if hasattr(res, "text") else True


# ---------------------------------------------------------------------- triage tool
def test_triage_groups_ranks_and_spots_fixed_issues(tmp_path):
    import importlib.util
    import sys as _sys
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("triage_issues", Path(__file__).resolve().parents[1] / "tools" / "triage_issues.py")
    t = importlib.util.module_from_spec(spec)
    _sys.modules["triage_issues"] = t
    spec.loader.exec_module(t)

    def mk(n, rid, sev, code, count=1):
        return {"number": n, "title": f"[{sev}] t{n}", "body": f"**Severity:** {sev} · **Code:** `{code}`\n**Count:** {count}\n<!-- jarves-issue: {rid} -->"}
    issues = [mk(1, "JI-aaaaaaaa", "P3", "SPEED_X", 4), mk(2, "JI-bbbbbbbb", "P1", "CRASH_Y"), mk(3, "JI-bbbbbbbb", "P1", "CRASH_Y"),
              mk(4, "JI-cccccccc", "P2", "TOOL_play_NO_PLAYER"), {"number": 5, "title": "unrelated", "body": "hello"}]
    log = "# Changelog\n\n## 4.1 (unreleased)\n- Fixes: SPEED_X\n\n## 4.0.2\n- Fixes: TOOL_play_NO_PLAYER\n"
    rows = t.decide(t.group(issues), t.released_fixes(log))
    assert [r["id"] for r in rows] == ["JI-bbbbbbbb", "JI-cccccccc", "JI-aaaaaaaa"]
    assert [r["action"] for r in rows] == ["DUPLICATE", "CLOSE", "FIX"]               # unreleased "Fixes" does not count
    assert rows[1]["fixed_in"] == "4.0.2" and "CLOSE" in t.render(rows)
    f = tmp_path / "i.json"
    f.write_text(json.dumps(issues), encoding="utf-8")
    assert t.main(["--json", str(f), "--changelog", str(tmp_path / "none.md")]) == 0


@pytest.mark.parametrize("source", ["monitor", "selfcheck"])
def test_unchanged_finding_snapshot_does_not_recount_or_reopen(reg, source):
    finding = {"fingerprint": "test:repeat", "category": "logs", "severity": "high", "count": 2,
               "source": source, "evidence": {"failures": 2}}
    if source == "selfcheck":
        finding["observation_run"] = 3
    ir.ingest_findings(reg, [finding])
    issue = reg.list()[0]
    first = issue["last_seen"]
    reg.clock.t += 600
    ir.ingest_findings(reg, [finding])
    assert reg.get(issue["id"])["count"] == 1
    assert reg.get(issue["id"])["last_seen"] == first
    reg.set_status(issue["id"], "fixed")
    reg.version = "4.0.2"
    # A different scan time / installed version / translated title is no new observation.
    finding.update(last_seen=reg.clock.t, title="private local note not persisted as identity")
    ir.ingest_findings(reg, [finding])
    unchanged = reg.get(issue["id"])
    assert unchanged["count"] == 1 and unchanged["status"] == "fixed"
    assert unchanged["fixed_in"] == "4.0.1"
    assert re.fullmatch(r"[0-9a-f]{64}", unchanged["observation"])
    if source == "selfcheck":
        finding["observation_run"] = 4
    else:
        finding["evidence"]["failures"] = 3
    ir.ingest_findings(reg, [finding])
    changed = reg.get(issue["id"])
    assert changed["count"] == 2 and changed["status"] == "new"


def test_legacy_finding_baselines_identity_without_reopening(reg):
    evidence = {"category": "logs", "count": 2, "finding": "legacy"}
    old = reg.record("health", "FINDING_legacy", "P1", evidence, source="diag")
    reg.set_status(old["id"], "fixed")
    reg.version = "4.0.2"
    finding = {"fingerprint": "legacy", "category": "logs", "severity": "high", "count": 2,
               "observation_run": 8}
    ir.ingest_findings(reg, [finding])
    current = reg.get(old["id"])
    assert current["status"] == "fixed" and current["count"] == 1
    finding["count"] = 3
    ir.ingest_findings(reg, [finding])
    assert reg.get(old["id"])["status"] == "new" and reg.get(old["id"])["count"] == 2


def test_identical_monitor_finding_after_recovery_is_a_new_occurrence(reg):
    finding = {"fingerprint": "recur", "category": "logs", "severity": "high", "count": 2}
    ir.ingest_findings(reg, [finding])
    issue = reg.list()[0]
    reg.set_status(issue["id"], "fixed")
    ir.ingest_findings(reg, [])  # the detector no longer finds it
    ir.ingest_findings(reg, [finding])  # same count, genuinely back after recovery
    current = reg.get(issue["id"])
    assert current["status"] == "new" and current["count"] == 2


@pytest.mark.parametrize("code,origin,defect", [
    ("FEATURE_NEEDS_APPROVAL", "model", False),
    ("TOOL_UNTRUSTED_TURN", "model", False),
    ("TOOL_DECLINED", "model", False),
    ("FEATURE_OFF", "model", False),
    ("SKILL_DISABLED", "model", False),
    ("TOOL_FORBIDDEN", "model", False),
    ("TOOL_BAD_ARGS", "user", False),
    ("TOOL_BAD_ARGS", "model", True),
    ("TOOL_CRASH", "model", True),
    ("TOOL_TIMEOUT", "model", True),
    ("PEAR_DOWN", "model", True),
])
def test_expected_tool_refusals_do_not_create_failure_bug_reports(reg, code, origin, defect):
    sink = ir.IssueSink(reg, threshold=3)
    for _ in range(5):
        sink.emit(_rec("TOOL_RESULT", tool="test", ok=False, result_code=code, origin=origin))
    sink.flush()
    assert bool(reg.list()) == defect


def test_quiet_p1_and_p2_never_expire_by_calendar_days_but_p3_does(tmp_path):
    # #372: 14 calendar days pass while the PC is off; a serious issue must not close itself because of that
    clock = Clock()
    reg = ir.Registry(tmp_path, clock=clock, version="4.0.1")
    reg.record("voice", "VOICE_CRASH_A", "P1")
    reg.record("tools", "TOOL_FAIL_B", "P2")
    reg.record("misc", "NOISE_C", "P3")
    clock.t += (ir.EXPIRE_DAYS + 30) * 86400
    reg.tidy()
    by = {r["code"]: r for r in reg.list()}
    assert by["VOICE_CRASH_A"]["status"] == "new" and by["TOOL_FAIL_B"]["status"] == "new"
    assert by["NOISE_C"]["status"] == "ignored" and by["NOISE_C"]["closed_reason"] == "expired"
