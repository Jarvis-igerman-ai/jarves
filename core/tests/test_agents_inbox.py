"""SW-V4 E (#49): the orchestrator's durable inbox/outbox and envelope on real SQLite (agents.db): order, dedup
(idempotent message_id), lease + retry with backoff, TTL expiry, cancellation, max attempts, hop limit, crash
recovery (a consumer that dies mid-lease; a reopened file), and the untrusted-text / payload allow-list rules."""
from __future__ import annotations

import time

import pytest

from jarves.orchestrator import db as dbm
from jarves.orchestrator.db import AgentDB
from jarves.orchestrator.envelope import (MAX_HOPS, EnvelopeError, clean, make, summary, validate,
                                          wrap_for_agent)


def env(sid="claude:s1", text="hi", **kw):
    return make("status_request", provider="claude", session_id=sid, payload={"text": text}, origin="owner", **kw)


def test_order_and_idempotent_enqueue(tmp_path):
    db = AgentDB(tmp_path / "agents.db")
    a, b, c = env(text="one"), env(text="two"), env(text="three")
    assert db.enqueue(a) and db.enqueue(b) and db.enqueue(c)
    assert db.enqueue(a) is False                         # the same message_id twice = stored once
    rows = db.claim("claude:s1", "hook-1")
    assert [r["payload"]["text"] for r in rows] == ["one", "two", "three"]
    assert db.ack([r["message_id"] for r in rows], delivery="stop_hook") == 3
    assert db.ack([a.message_id]) == 0                    # acked once only
    assert db.claim("claude:s1", "hook-2") == []
    assert db.claim("claude:other", "x") == []            # another session never sees them


def test_lease_retry_backoff_and_max_attempts(tmp_path, monkeypatch):
    db = AgentDB(tmp_path / "agents.db")
    e = env()
    db.enqueue(e)
    t0 = time.time()
    assert len(db.claim("claude:s1", "c1", now=t0)) == 1          # leased
    assert db.claim("claude:s1", "c2", now=t0 + 1) == []           # still leased to c1
    again = db.claim("claude:s1", "c2", now=t0 + dbm.CLAIM_S + 1)  # c1 died: the lease ran out -> offered again
    assert len(again) == 1 and again[0]["attempts"] == 2
    now = t0 + dbm.CLAIM_S + 1
    for _ in range(dbm.MAX_ATTEMPTS):
        now += dbm.CLAIM_S + max(dbm.BACKOFF_S) + 1
        db.claim("claude:s1", "cx", now=now)
    assert db.message(e.message_id)["state"] == "failed"
    assert db.pending("claude:s1") == []


def test_ttl_expiry_and_cancel(tmp_path):
    db = AgentDB(tmp_path / "agents.db")
    old = make("command", provider="claude", session_id="claude:s1", payload={"text": "x"},
               timestamp=time.time() - 10, expires_at=time.time() - 1)
    keep = env(text="keep")
    gone = env(text="gone")
    for e in (old, keep, gone):
        db.enqueue(e)
    assert db.cancel(gone.message_id) is True and db.cancel(gone.message_id) is False
    rows = db.claim("claude:s1", "c")
    assert [r["payload"]["text"] for r in rows] == ["keep"]
    assert db.message(old.message_id)["state"] == "expired"


def test_crash_recovery_reopened_file_redelivers_unacked_once(tmp_path):
    p = tmp_path / "agents.db"
    db = AgentDB(p)
    e = env(text="survive")
    db.enqueue(e)
    t0 = time.time()
    db.claim("claude:s1", "dying-consumer", now=t0)
    db.close()                                             # the process dies before its ack
    db2 = AgentDB(p)
    rows = db2.claim("claude:s1", "next", now=t0 + dbm.CLAIM_S + 1)
    assert [r["message_id"] for r in rows] == [e.message_id]
    db2.ack([e.message_id])
    assert db2.claim("claude:s1", "again", now=t0 + 10 * dbm.CLAIM_S) == []    # delivered once
    db2.close()


def test_hop_limit_and_envelope_validation():
    with pytest.raises(EnvelopeError, match="HOP_LIMIT"):
        validate({**env().to_dict(), "hops": MAX_HOPS + 1})
    with pytest.raises(EnvelopeError):
        validate({**env().to_dict(), "kind": "rm -rf"})
    with pytest.raises(EnvelopeError):
        validate({**env().to_dict(), "schema_version": 99})
    with pytest.raises(EnvelopeError):
        validate({**env().to_dict(), "message_id": "../../x"})
    ok = validate({**env().to_dict(), "hops": MAX_HOPS, "origin": "root", "severity": "apocalyptic"})
    assert ok.origin == "agent" and ok.severity == "info"   # unknown origin / severity fall back, never trusted


def test_payload_is_allow_listed_and_cleaned():
    e = validate({**env().to_dict(), "payload": {
        "text": "a‮evil​ text\x07 sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "api_key": "sk-secret", "unknown_key": "dropped", "files": ["a.py", "b.py"],
        "summary": "```python\nimport os\nos.system('x')\n```\ndone"}})
    assert "api_key" not in e.payload and "unknown_key" not in e.payload
    t = e.payload["text"]
    assert "‮" not in t and "​" not in t and "\x07" not in t
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in t      # secret redacted (logs.redact)
    assert e.payload["files"] == ["a.py", "b.py"]
    assert "os.system" in e.payload["summary"]               # payload keeps code (display), voice summary drops it
    assert "os.system" not in summary(e.payload["summary"])
    assert len(clean("x" * 5000, 100)) == 100


def test_wrap_for_agent_marks_owner_messages_and_never_approves():
    rows = [{"kind": "status_request", "message_id": "m1", "correlation_id": "c1", "payload": {"text": "what blocks you?"}},
            {"kind": "command", "message_id": "m2", "correlation_id": "", "payload": {"text": "run the tests"}}]
    w = wrap_for_agent(rows)
    assert "correlation_id=c1] The owner asks: what blocks you?" in w
    assert "correlation_id=m2] The owner says: run the tests" in w
    assert "not permission approvals" in w and "jarves_reply" in w


def test_counters_and_audit_retention(tmp_path):
    db = AgentDB(tmp_path / "agents.db")
    assert db.bump("turn") == 1 and db.bump("turn") == 2 and db.count("turn") == 2
    db.audit("owner", "approval", session_id="claude:s", decision="deny", result="Bash", detail="git push")
    db._x("INSERT INTO audit (ts, who, what) VALUES (?, 'old', 'x')", (time.time() - 100 * 86400,))
    db.prune()
    rows = db.audit_rows()
    assert [r["who"] for r in rows] == ["owner"]               # 90-day retention
