"""CodeQL #13, #19, #20, #21 (py/clear-text-storage/logging-sensitive-data): sanitizer proofs.

CodeQL cannot model JARVES's redaction (logs.redact / redact_deep / the field registry / the formatters), so these
tests pin the evidence for each flagged sink, in BOTH privacy modes, with synthetic planted values (tools/
redaction_audit.py: no real credential). A failure here means a real leak, not a test to relax.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarves import approvals, cli, config, logs
from jarves.brain import billing_routes

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
from tools import redaction_audit as ra  # noqa: E402

MODES = [pytest.param(True, id="privacy_on"), pytest.param(False, id="privacy_off")]


def classes(data: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, enc in ra.scan_bytes(data.encode("utf-8")):
        out.setdefault(key.split(":")[0], []).append(f"{key}@{enc}")
    return out


# ------------------------------------------------------------------------------------- #13 approvals audit.jsonl
@pytest.mark.parametrize("private", MODES)
def test_13_approvals_audit_record_never_stores_a_secret(tmp_path, private, monkeypatch):
    """The real writer path (ApprovalService._audit_simple -> append_audit -> audit.jsonl): secrets planted in the
    title, prompt, registered and unregistered detail values, nested values and credential-named keys."""
    monkeypatch.setattr(logs, "_privacy", private)
    svc = approvals.ApprovalService(tmp_path)
    s = ra.SECRETS
    details = {"to": ra.PII["email"], "body": ra.MIXED, "subject": f"re {s['google_refresh']}",
               "never_registered": f"x {s['telegram_bot']}", "nested": {"deep": [s["github_token"], ra.PII["phone_intl"]]},
               "api_key": s["openai_key"], "password": s["password"], "authorization": f"Bearer {s['bridge_bearer']}"}
    svc._audit_simple(f"email {ra.PII['english_name']} {s['openai_key']}", f"send it {s['meta_token']}", "email",
                      "high", "model", details, "approved", "dashboard", "a1", 1.0)
    svc._audit_simple("run", f"curl -H 'Authorization: Bearer {s['bridge_bearer']}' --token {s['github_token']}",
                      "shell", "high", "model", details, "denied", "popup", "a2", 1.0)
    lines = (tmp_path / "approvals" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        c = classes(line)
        assert not c.get("secret"), c
        rec = json.loads(line)
        assert not {"api_key", "password", "authorization"} & set(rec["details"])      # credential keys dropped
    email = classes(lines[0])
    if private:
        assert not email.get("pii") and not email.get("content"), email
    assert "curl" in json.loads(lines[1])["prompt"]           # the shell command stays readable (security record)


# ------------------------------------------------------------------------------------- #19 chat CLI /model
def test_19_the_model_billing_line_only_carries_route_labels():
    """chat_cli.py:455 prints state["billing"] (Core snapshot = billing_routes()). Every combination yields only the
    fixed route labels, so the terminal line cannot carry a key or personal data."""
    labels = {"api_key", "chatgpt_plan", "local", "off"}
    keys = {"typed_text", "spoken_voice", "voice_backend", "reply_speech"}
    for provider, conn, deleg, mode, engine in itertools.product(
            ("openai", "local"), ("api_key", "chatgpt", "combined"), ("server", "client"),
            (None, "live", "push_to_talk", "off"), ("auto", "none", "local")):
        ai = SimpleNamespace(provider=provider, connection=conn, delegation_mode=deleg)
        voice = None if mode is None else SimpleNamespace(mode=mode, tts_engine=engine)
        r = billing_routes(ai, voice)
        assert set(r) == keys and set(r.values()) <= labels, r


# ------------------------------------------------------------------------------------- #20 jarvis set-key
def test_20_ask_key_prints_the_file_location_never_the_key(tmp_path, monkeypatch, capsys):
    """cli.py:991 prints save_secret()'s return value: the secrets.env Path, not the key (save_secret -> Path)."""
    key = ra.SECRETS["openai_key"]
    monkeypatch.setenv("OPENAI_API_KEY", "")                  # save_secret sets it; monkeypatch restores it
    monkeypatch.setattr("getpass.getpass", lambda prompt="": key)
    assert cli._ask_key(tmp_path) == 0
    out = capsys.readouterr()
    assert key not in out.out and key not in out.err and not classes(out.out + out.err).get("secret")
    assert f"saved to {config.secrets_file(tmp_path)}" in out.out
    assert key in config.secrets_file(tmp_path).read_text(encoding="utf-8")       # it went to the private file


# ------------------------------------------------------------------------------------- #21 central log sink
@pytest.mark.parametrize("private", MODES)
def test_21_log_sink_real_file_and_console_handlers(tmp_path, private, capsys):
    """logs.py:793 hands arbitrary `fields` to the logger; the handlers installed by logs.setup() (JSON file AND the
    real stderr console handler) redact them. Mirrors the flagged flows: campaign token=/text=, unregistered and
    nested fields, an exception message."""
    path = logs.setup(tmp_path / "logs", privacy=private, console=True, console_level="DEBUG")
    try:
        s = ra.SECRETS
        logs.info("campaign", "CAMPAIGN_PLANT", "planted secrets log line", token=s["github_token"], text=ra.MIXED)
        logs.info("planted", "PLANTED_UNKNOWN", "unregistered", zz_unregistered=f"k {s['openai_key']}",
                  zz_nested={"a": [s["telegram_bot"], {"b": s["google_access"]}]}, api_key=s["openai_key"])
        logs.warn("planted", "PLANTED_ERR", "provider error", **logs.error_fields(RuntimeError(f"{s['meta_token']}")))
        for h in logs._logger.handlers:
            h.flush()
        console = capsys.readouterr().err
        raw = path.read_text(encoding="utf-8")
    finally:
        logs.setup(tmp_path / "logs2", privacy=True, console=False)
        logs.set_privacy(True)
    assert "CAMPAIGN_PLANT" in raw and "CAMPAIGN_PLANT" in console          # both sinks really received the lines
    for blob in (raw, console):
        c = classes(blob)
        assert not c.get("secret"), c
        if private:
            assert not c.get("pii") and not c.get("content"), c
