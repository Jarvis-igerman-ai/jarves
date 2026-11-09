"""Action receipts for all tools (V7-X1 #420): fake registry hook, frozen clock, no real tools, no network."""
from __future__ import annotations

import asyncio
import json
import types

from jarves import action_receipts as ar

SECRET = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def mk(tmp_path, on=True):
    state = {"on": on}
    return ar.ReceiptLog(tmp_path, lambda: state["on"], wall=lambda: 1000.0), state


def lines(tmp_path):
    return [json.loads(x) for x in (tmp_path / ar.RECEIPT_FILE).read_text(encoding="utf-8").splitlines()]


def test_off_writes_nothing(tmp_path):
    log, _ = mk(tmp_path, on=False)
    assert log.record("clock", True, "OK") is None
    assert not (tmp_path / ar.RECEIPT_FILE).exists()


def test_receipt_has_only_safe_fields(tmp_path):
    log, _ = mk(tmp_path)
    log.record("clock", True, "OK", "user", 12)
    log.record(SECRET + " https://x.example/?q=1", False, SECRET, "weird", -5)
    a, b = lines(tmp_path)
    want = {"seq": 1, "ts": 1000.0, "tool": "clock", "ok": True, "code": "OK", "origin": "user", "ms": 12}
    assert {k: a[k] for k in want} == want                 # (privacy mode may add empty title/details)
    assert b["tool"] == "other" and b["code"] == "" and b["origin"] == "other" and b["ms"] == 0
    assert SECRET not in (tmp_path / ar.RECEIPT_FILE).read_text(encoding="utf-8")


def test_write_failure_is_swallowed(tmp_path, monkeypatch):
    log, _ = mk(tmp_path)
    monkeypatch.setattr(ar, "append_audit", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert log.record("clock", True) is None


def test_registry_records_every_call_but_never_args_or_result(tmp_path):
    from jarves.skills import SkillRegistry, ToolResult
    reg = SkillRegistry()
    log, state = mk(tmp_path)
    reg.receipts = log

    async def fake_call(name, raw_args=None, *, origin="model"):
        return ToolResult(True, "result text " + SECRET, code="OK")
    reg._call = fake_call
    asyncio.run(reg.call("lights", {"token": SECRET}))
    state["on"] = False
    asyncio.run(reg.call("lights", {}))                  # switched off live: no receipt
    rows = lines(tmp_path)
    assert len(rows) == 1 and rows[0]["tool"] == "lights" and rows[0]["ok"] is True and rows[0]["origin"] == "model"
    assert SECRET not in json.dumps(rows)


def test_default_registry_has_no_receipts_and_behaviour_unchanged():
    from jarves.skills import SkillRegistry, ToolResult
    reg = SkillRegistry()
    assert reg.receipts is None

    async def fake_call(name, raw_args=None, *, origin="model"):
        return ToolResult(False, "no", code="X")
    reg._call = fake_call
    assert asyncio.run(reg.call("t")).code == "X"


def test_feature_off_by_default_and_beta():
    from jarves import features as feat
    f = feat.CATALOG[ar.FEATURE]
    assert f.default_enabled is False and feat.stage_of(ar.FEATURE) == "beta"


def test_cli_status_list_clear(tmp_path, capsys, monkeypatch):
    log, _ = mk(tmp_path)
    log.record("clock", True, "OK", "model", 3)
    monkeypatch.setattr("jarves.cli._cli_features", lambda cfg, home: types.SimpleNamespace(active=lambda f: True))
    ns = lambda **k: types.SimpleNamespace(limit=20, json=True, **k)
    assert ar.cmd_receipts(ns(action="list"), None, tmp_path) == 0
    assert json.loads(capsys.readouterr().out)[0]["tool"] == "clock"
    assert ar.cmd_receipts(ns(action="status"), None, tmp_path) == 0
    assert json.loads(capsys.readouterr().out) == {"action_receipts": "on", "stored": 1}
    ar.cmd_receipts(ns(action="clear"), None, tmp_path)
    assert ar.read(tmp_path) == []
