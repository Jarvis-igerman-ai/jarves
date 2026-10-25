"""Issue #17: the issues registry and the diagnose output are readable and honest. Every title is plain English and
Hebrew (never an internal key), version findings are one information item, expected environment states are information
(not counted as bugs, ignorable on this PC), old issues close themselves ('not seen since X', history kept), the diagnose
output has one line per cause with Hebrew kept off the English line, and the CLI can list/export in UTF-8. Offline."""
from __future__ import annotations

import json
import re

import pytest

from jarves import diagnose, issue_registry as ir, issue_text as it
from test_issue_registry import Clock, _cli, reg  # noqa: F401  (fixtures and helpers)

HEB = re.compile(r"[\u0590-\u05ff]")
RAW = re.compile(r"^(FINDING_|[A-Z][A-Z0-9]*_[A-Z0-9_]+$|[a-z]+:[A-Za-z0-9_.:]+$)")


def _finding(fp, title="", sev="medium", cat="logs", **kw):
    return {"fingerprint": fp, "severity": sev, "category": cat, "title": title, "title_he": kw.pop("title_he", ""), **kw}


def test_every_title_is_readable_english_and_hebrew_never_a_raw_key(reg):
    ir.ingest_findings(reg, [
        _finding("anomaly:wake_false"), _finding("health:something_odd"), _finding("anomaly:newcode:CHAT_TURN_FAILED"),
        _finding("log:WARNING:SOME_CODE", "Repeated failure: SOME_CODE 17x across 14 runs"),
        _finding("trend:handles", "x " * 80)])           # an over-long title is cut, not replaced by the key
    reg.record("core", "SOME_UNKNOWN_KEY", "P3")
    rows = reg.list(None)
    assert len(rows) == 6
    for r in rows:
        d = it.describe(r)
        assert not RAW.match(d["title"]), (r["code"], d["title"])
        assert HEB.search(d["title_he"]) and d["meaning"] and d["next_step"] and d["next_step_he"]
        assert d["evidence_count"] >= 1
    cut = reg.get(ir.issue_id("health", "FINDING_trend:handles"))
    assert len(cut["title"]) <= 100 and cut["title"].endswith("\u2026")


def test_rates_in_titles_are_kept_and_paths_are_still_refused(reg):
    r = reg.record("core", "X_CODE", "P3", title="X_CODE is 3x more frequent (0.5/h -> 2/h)")
    assert r["title"] == "X_CODE is 3x more frequent (0.5 per h to 2 per h)"
    assert reg.record("core", "Y_CODE", "P3", title="see C:\\Users\\me\\a.txt")["title"] == "Y_CODE"


def test_version_findings_are_one_information_item(reg):
    ir.ingest_findings(reg, [_finding("version:4.0.1.212", sev="high"), _finding("version:4.0.1.217"),
                             _finding("version:4.0.1.278")])
    ir.ingest_findings(reg, [_finding("version:4.0.1.278")])                    # the same version again: nothing new
    rows = reg.list(None)
    assert [r["code"] for r in rows] == ["INFO_NEW_VERSION"]
    assert rows[0]["evidence"]["versions"] == ["4.0.1.212", "4.0.1.217", "4.0.1.278"] and rows[0]["count"] == 3
    assert it.describe(rows[0])["kind"] == "info" and reg.counts()["open"] == 0 and reg.counts()["info"] == 1


def test_old_per_version_records_are_merged_on_tidy(reg):
    for v in ("4.0.1.212", "4.0.1.217", "4.0.1.278"):
        reg.record("health", f"FINDING_version:{v}", "P1", {"finding": f"version:{v}"})
    assert reg.counts()["info"] == 3
    reg.tidy("")
    assert [r["code"] for r in reg.list(None)] == ["INFO_NEW_VERSION"]
    assert reg.get(ir.issue_id("health", "INFO_NEW_VERSION"))["evidence"]["versions"][-1] == "4.0.1.278"


@pytest.mark.parametrize("fp,title", [
    ("log:WARNING:LINK_FACE_DISCONNECTED", "Repeated failure: LINK_FACE_DISCONNECTED 17x across 14 runs"),
    ("health:face_link", "face_link is unavailable: waiting for the Godot face to connect"),
    ("avail:camera", "The camera was not available"),
    ("avail:pear", "Pear Desktop (the music player) was not reachable")])
def test_environment_states_are_information_not_bugs(reg, fp, title):
    ir.ingest_findings(reg, [_finding(fp, title, sev="high"), _finding("anomaly:newcode:REAL_BUG", sev="medium")])
    c = reg.counts()
    assert c["open"] == 1 and c["info"] == 1 and c["total"] == 2                      # the header count excludes it
    info = reg.list("info")[0]
    d = it.describe(info)
    assert d["kind"] == "info" and d["hint"] and d["hint_he"] and d["next_step"]
    assert [r["code"] for r in reg.list("open")] == ["FINDING_anomaly:newcode:REAL_BUG"]
    reg.set_status(info["id"], "ignored")                                           # "ignore this on this PC"
    ir.ingest_findings(reg, [_finding(fp, title)])
    assert reg.get(info["id"])["status"] == "ignored" and reg.counts()["info"] == 0


def test_auto_resolve_by_fixes_line_with_the_code_inside_a_finding_key(tmp_path):
    clock = Clock()
    old = ir.Registry(tmp_path, clock=clock, version="4.0.1.100")
    ir.ingest_findings(old, [_finding("anomaly:newcode:CHAT_TURN_FAILED", "New error code CHAT_TURN_FAILED: 3x (never seen in 14 days)")])
    other = old.record("core", "STILL_BROKEN", "P2")
    new = ir.Registry(tmp_path, clock=clock, version="4.0.1.278")
    closed = new.tidy("## 4.0.1\n- Fixes: CHAT_TURN_FAILED (web chat)\n")
    r = new.get(ir.issue_id("health", "FINDING_anomaly:newcode:CHAT_TURN_FAILED"))
    assert [x["id"] for x in closed] == [r["id"]] and r["status"] == "fixed" and r["fixed_in"] == "4.0.1.278"
    assert r["count"] == 1 and r["versions"] == ["4.0.1.100"]                       # history is kept
    assert "4.0.1.278" in it.describe(r)["closed"]
    assert new.get(other["id"])["status"] == "new"


def test_a_fix_listed_for_the_version_that_still_sees_the_issue_does_not_close_it(tmp_path):
    clock = Clock()
    cur = ir.Registry(tmp_path, clock=clock, version="4.0.1.278")
    r = cur.record("core", "CHAT_TURN_FAILED", "P2")
    assert cur.tidy("- Fixes: CHAT_TURN_FAILED\n") == [] and cur.get(r["id"])["status"] == "new"


def test_auto_resolve_when_it_did_not_come_back_after_an_update(tmp_path):
    clock = Clock()
    old = ir.Registry(tmp_path, clock=clock, version="4.0.1.100")
    r = old.record("tools", "TOOL_x_FAIL", "P2")
    cur = ir.Registry(tmp_path, clock=clock, version="4.0.1.278")
    assert cur.tidy("") == []                                                         # the update was just seen
    clock.t += 2 * 86400
    assert cur.tidy("") == []                                                         # not long enough
    clock.t += 2 * 86400
    (hit,) = cur.tidy("")
    got = cur.get(r["id"])
    assert hit["id"] == r["id"] and got["status"] == "fixed" and got["closed_reason"] == "quiet"
    d = it.describe(got)
    assert d["closed"] == "Closed: not seen since 4.0.1.100" and "4.0.1.100" in d["closed_he"]
    assert cur.take_unannounced() == []                                               # closing quietly is not announced
    cur.record("tools", "TOOL_x_FAIL", "P2")                                          # it comes back: reopened
    assert cur.get(r["id"])["status"] == "new"
    again = cur.record("tools", "TOOL_y_FAIL", "P2")                                  # keeps happening: never closed
    clock.t += 5 * 86400
    assert cur.tidy("") == [] and cur.get(again["id"])["status"] == "new"


def test_fixed_with_version_and_note(tmp_path):
    rc, text = _cli(tmp_path, "add", "MY_PROBLEM")
    iid = re.search(r"JI-[0-9a-f]{8}", text).group(0)
    rc, text = _cli(tmp_path, "fixed", iid, "--in", "4.0.1.300", "--note", "web chat retry")
    assert rc == 0 and "fixed in 4.0.1.300" in text
    r = ir.Registry(tmp_path).get(iid)
    assert r["fixed_in"] == "4.0.1.300" and r["fixed_note"] == "web chat retry"
    assert "web chat retry" in _cli(tmp_path, "show", iid)[1]
    assert _cli(tmp_path, "fixed", iid, "--note", "C:\\Users\\me\\x.txt")[0] == 2     # private text is refused


def test_cli_list_options_json_and_utf8_export(tmp_path, monkeypatch):
    monkeypatch.setattr(ir, "EXPIRE_DAYS", 1e9)       # the fake clock is in 1970 and the CLI reads with the real one
    clock = Clock()
    reg = ir.Registry(tmp_path, clock=clock, version="4.0.1.278")
    ir.ingest_findings(reg, [_finding("anomaly:newcode:REAL_BUG", "New error code REAL_BUG", title_he="קוד שגיאה חדש REAL_BUG"),
                             _finding("avail:camera", "The camera was not available")])
    old = reg.record("core", "OLD_ONE", "P3", now=1.0)
    reg.set_status(old["id"], "fixed")
    head = _cli(tmp_path, "list")[1]
    assert head.startswith("1 open issue(s), 1 information item(s)") and "REAL_BUG" in head
    opened = _cli(tmp_path, "list", "--open")[1].split("\n", 1)[1]
    assert "REAL_BUG" in opened and "camera" not in opened.lower()
    allv = json.loads(_cli(tmp_path, "list", "--all", "--json")[1])
    assert len(allv["issues"]) == 3 and allv["counts"]["open"] == 1
    assert {i["kind"] for i in allv["issues"]} == {"bug", "info"}
    assert all(not RAW.match(i["title"]) for i in allv["issues"])
    assert [i["kind"] for i in json.loads(_cli(tmp_path, "list", "--info", "--json")[1])["issues"]] == ["info"]
    recent = json.loads(_cli(tmp_path, "list", "--all", "--since", "2026-01-01", "--json")[1])["issues"]
    assert len(recent) == 0                                    # the fake clock is in 1970: nothing is that recent
    assert _cli(tmp_path, "list", "--since", "banana")[0] == 2
    target = tmp_path / "sub" / "issues.json"
    rc, text = _cli(tmp_path, "export", "--all", "--out", str(target))
    raw = target.read_bytes()
    assert rc == 0 and not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
    assert "קוד שגיאה חדש" in raw.decode("utf-8") and len(json.loads(raw.decode("utf-8"))["issues"]) == 3
    txt = tmp_path / "issues.txt"
    assert _cli(tmp_path, "export", "--format", "text", "--out", str(txt))[0] == 0
    assert "REAL_BUG" in txt.read_bytes().decode("utf-8")


def test_parse_since():
    assert ir.parse_since("7d", 1_000_000) == 1_000_000 - 7 * 86400 and ir.parse_since("12h", 100000) == 100000 - 43200
    assert ir.parse_since("2026-10-20") == 1792454400.0 and ir.parse_since("") is None


def test_dashboard_api_carries_readable_fields_and_info_filter(tmp_path):
    reg = ir.Registry(tmp_path)
    ir.ingest_findings(reg, [_finding("avail:camera", "The camera was not available"), _finding("anomaly:wake_false", sev="high")])

    class Ctx:
        home = tmp_path
    code, body = ir.api_list(Ctx, None, None, {"status": ["info"]})
    assert code == 200 and [i["kind"] for i in body["issues"]] == ["info"] and body["issues"][0]["hint"]
    code, body = ir.api_list(Ctx, None, None, {"status": ["open"]})
    assert body["issues"][0]["title"] == "Too many false wake-ups per day" and body["counts"]["open"] == 1
    assert ir.api_count(Ctx, None, None)[1]["open"] == 1


# ---------------------------------------------------------------------- diagnose output
def _row(fp, sev, title, count=1, ts=1.0):
    return {"fingerprint": fp, "severity": sev, "title": title, "count": count, "last_seen": ts}


def test_diagnose_merges_by_cause_and_keeps_severity_honest():
    rows = [_row("silent_wait", "medium", "Dead air while waiting: 9 s of silence", 2),
            _row("dead_air", "high", "Dead air before replies: 3x over 4 s (worst 13.0 s)", 3),
            _row("tool_fail:music_play", "medium", "Tool music_play failed 2x", 1),
            _row("tool_repeat:music_play", "low", "Tool music_play called 3x within 30 s", 1),
            _row("event:LINK_FACE_DISCONNECTED", "high", "LINK_FACE_DISCONNECTED: Godot face disconnected", 17)]
    s = diagnose.by_cause(rows)
    assert [r["cause"] for r in s["problems"]] == ["dead_air", "tool:music_play"]
    top = s["problems"][0]
    assert top["severity"] == "high" and top["count"] == 5 and top["merged"] == 2 and top["title"].startswith("Dead air before")
    assert [r["cause"] for r in s["info"]] == ["event:LINK_FACE_DISCONNECTED"] and s["info"][0]["hint"]


def test_diagnose_line_never_mixes_hebrew_into_the_english_line():
    title = 'Confusing filler "רוגס" said 4x - the user asked what it means'
    lines = diagnose.render_summary([{"fingerprint": "filler:x"}], diagnose.by_cause([_row("filler:x", "high", title, 1)]))
    assert not HEB.search(lines[1])                                                   # the English line is pure ASCII
    assert lines[1] == '  [high  ] Confusing filler "#1" said 4x - the user asked what it means  (x1)'
    assert lines[2].strip() == "#1: רוגס" and "1 problem(s)" in lines[0]
    assert it.split_mixed("plain english") == ("plain english", [])
    assert it.isolate("said רוגס now").count(it.FSI) == 1 and it.isolate("רוגס") == "רוגס"
