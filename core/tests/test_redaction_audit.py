"""SW-V4 package B acceptance (system level): tools/redaction_audit.py drives a real Core (app.Core on a throw-away
TEST home, offline fakes) with planted secrets and personal data at every input - typed turns, tool errors, a provider
error injected by the fake agent, GPT-Live provider errors, a third-party library warning, crashes, approvals, a
background task, health text, every logs.* call site, log rotation, the dashboard log view and download,
`status --json`, a self-check ticket draft, a local backup and a `jarvis diag` bundle - and searches every output in
raw / JSON / URL / base64 / UTF-16LE / NFC / NFD form.

0 secret hits in every sink in both modes; 0 content/pii hits in every diagnostic sink in privacy mode; 0 content/pii
hits in the bundle in both modes. In development mode the planted content must reach the log and the history store
(positive control)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
from tools import redaction_audit as ra  # noqa: E402


@pytest.mark.parametrize("private", [pytest.param(True, id="privacy_on"), pytest.param(False, id="privacy_off")])
def test_planted_data_audit_of_a_real_core(tmp_path, private):
    report = ra.audit(private, tmp_path)
    assert report["synthetic_call_sites"] >= 450
    sinks = report["sinks"]
    for must in ("logs/jarves.log", "logs/jarves.log.1", "store.db", "health.json", "approvals/audit.jsonl",
                 "tasks.jsonl", "logs/crash.txt", "<console>", "<status_json>", "<dashboard/api/logs>",
                 "<dashboard/export/logs.jsonl>", "<diag_zip>", "<ticket_draft>"):
        assert must in sinks, must
    assert any(n.startswith("backups/") and n.endswith(".zip") for n in sinks)
    assert not report["violations"], "\n".join(ra.mask_violation(v) for v in report["violations"])


def test_violation_lines_are_masked():
    leaked = "ya29.PLANTEDg00gl3acc3ssT0k3n@plain"       # FAKE test fixture, synthetic
    line = ra.mask_violation(f"logs/jarves.log: SECRET ['google_access:{leaked}']")
    assert "PLANTED" not in line and leaked not in line and line.startswith("logs/jarves.log: SECRET ['go")
    msg = "store.db: sink was not produced by the drive"
    assert ra.mask_violation(msg) == msg
