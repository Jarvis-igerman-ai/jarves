"""SW-V4 package B bundle rule: `jarvis diag` ALWAYS strips content and hashes pii, whatever log.privacy was when the
log was written - nested JSON, malformed lines (dropped and counted, never copied), stack traces (frames kept, local
values and exception text dropped), older free-text lines and the SQLite export (store summary)."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

from jarves import diagbundle, logs
from jarves.config import Config

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
from tools import redaction_audit as ra  # noqa: E402


def write_dev_log(home: Path) -> Path:
    """A log written in DEVELOPMENT mode (privacy off): full content, plus old-format and malformed lines."""
    path = logs.setup(home / "logs", privacy=False, console=False, max_bytes=40_000, backups=3)
    try:
        for i in range(60):
            logs.info("live", "LIVE_USER_SAID", "you said", text=ra.MIXED, n=i)
            logs.info("core", "REPLY", "reply sent", utterance=ra.MIXED, text=ra.CONTENT["hebrew_body"],
                      to=ra.PII["email"], profile=ra.PII["english_name"], source="ai", ms=120)
            logs.warn("live", "LIVE_ERROR", "the voice provider reported an error", provider_error=ra.MIXED,
                      error_code="invalid_value", stats={"detail": ra.MIXED, "drops": 2, "model": "gpt-live-1"})
        try:
            raise ValueError(ra.MIXED)
        except ValueError:
            logs.error("core", "BOOM", "it failed", exc=True)
        for h in logs._logger.handlers:
            h.flush()
    finally:
        logs.set_privacy(True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-10-19T10:00:00", "level": "ERROR", "component": "voice", "code": "OLD",
                            "msg": f"Live error: {ra.MIXED}", "exc": f"KeyError: {ra.MIXED}",
                            "nested": {"a": [{"b": ra.MIXED}]}}, ensure_ascii=False) + "\n")
        f.write(f"half a line {ra.MIXED}\n")                       # malformed
        f.write(json.dumps([ra.MIXED]) + "\n")                        # JSON but not a record: malformed too
    return path


def build(home, tmp_path, name="d.zip"):
    out = tmp_path / name
    man = diagbundle.build(home, Config(), out, lines=5000)
    with zipfile.ZipFile(out) as z:
        members = {n: z.read(n).decode("utf-8") for n in z.namelist()}
    return man, members


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("JARVES_HOME", str(h))
    monkeypatch.setenv("OPENAI_ADMIN_KEY", ra.SECRETS["openai_admin_key"])
    return h


def test_a_development_log_ships_without_content_pii_or_secrets(home, tmp_path):
    write_dev_log(home)
    raw = "".join(p.read_text(encoding="utf-8") for p in (home / "logs").glob("jarves.log*"))
    assert ra.scan_bytes(raw.encode("utf-8"))                         # positive control: the log HAS the content
    man, members = build(home, tmp_path)
    for name, text in members.items():
        assert not ra.scan_bytes(text.encode("utf-8")), (name, ra.scan_bytes(text.encode("utf-8")))
    assert man["log_lines_dropped_malformed"] == 2 and "half a line" not in members["recent.log"]
    recs = [json.loads(x) for x in members["recent.log"].splitlines()]
    reply = next(r for r in recs if r["code"] == "REPLY")
    # template, ids and timings stay rich; content is a length; pii a per-bundle pseudonym
    assert reply["msg"] == "reply sent" and reply["source"] == "ai" and reply["ms"] == 120
    assert reply["utterance"].startswith("<") and reply["text"].startswith("<")
    assert reply["to"].startswith("pii:") and reply["profile"].startswith("pii:")
    err = next(r for r in recs if r["code"] == "LIVE_ERROR")
    assert err["error_code"] == "invalid_value" and err["provider_error"].startswith("<")
    assert err["stats"]["drops"] == 2 and err["stats"]["model"] == "gpt-live-1" and err["stats"]["detail"].startswith("<")
    boom = next(r for r in recs if r["code"] == "BOOM")
    assert boom["exc_type"] == "ValueError" and boom["exc_frames"] and boom["exc_text"].startswith("<")
    old = next(r for r in recs if r["code"] == "OLD")
    assert old["msg"].startswith("<") and old["exc"].startswith("<") and old["nested"].startswith("<")


def test_pii_pseudonyms_correlate_inside_one_bundle_only(home, tmp_path):
    write_dev_log(home)
    _, a = build(home, tmp_path, "a.zip")
    _, b = build(home, tmp_path, "b.zip")
    to_a = {json.loads(x).get("to") for x in a["recent.log"].splitlines() if '"REPLY"' in x}
    to_b = {json.loads(x).get("to") for x in b["recent.log"].splitlines() if '"REPLY"' in x}
    assert len(to_a) == 1 and len(to_b) == 1 and to_a != to_b        # same address -> same pseudonym, new salt


def test_store_export_and_config_members_hold_no_personal_text(home, tmp_path):
    from jarves.store import Store
    st = Store(home / "store.db")
    st.x("INSERT INTO issues (fingerprint, kind, severity, title, detail, evidence, first_seen, last_seen) "
         "VALUES (?,?,?,?,?,?,?,?)", ("f1", "rule", "high", f"user {ra.PII['hebrew_name']} said {ra.MIXED}",
                                      ra.MIXED, json.dumps([ra.MIXED]), 1.0, 2.0))
    st.commit()
    st.close()
    (home / f"{ra.PII['english_name']}.txt").write_text("x", encoding="utf-8")      # a personal file name
    man, members = build(home, tmp_path)
    summary = json.loads(members["store_summary.json"])
    assert summary["open_issues"][0]["severity"] == "high" and summary["open_issues"][0]["title"].startswith("<")
    for name, text in members.items():
        assert not ra.scan_bytes(text.encode("utf-8")), name
    info = json.loads(members["info.json"])
    assert info["keys_present"]["openai_admin"] is True                # presence only, never the value


def test_a_privacy_mode_log_ships_clean_too(home, tmp_path):
    path = logs.setup(home / "logs", privacy=True, console=False)
    logs.info("core", "REPLY", "reply sent", utterance=ra.MIXED, text=ra.MIXED, to=ra.PII["email"])
    for h in logs._logger.handlers:
        h.flush()
    assert not ra.scan_bytes(path.read_bytes())
    _, members = build(home, tmp_path)
    assert all(not ra.scan_bytes(t.encode("utf-8")) for t in members.values())
