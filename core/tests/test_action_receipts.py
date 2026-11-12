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
    from jarves.skills.builtin import ClockSkill
    reg = SkillRegistry()
    reg.register(ClockSkill())
    log, state = mk(tmp_path)
    reg.receipts = log

    async def fake_call(name, raw_args=None, *, origin="model"):
        return ToolResult(True, "result text " + SECRET, code="OK")
    reg._call = fake_call
    asyncio.run(reg.call("get_time", {"token": SECRET}))
    state["on"] = False
    asyncio.run(reg.call("get_time", {}))                  # switched off live: no receipt
    rows = lines(tmp_path)
    assert len(rows) == 1 and rows[0]["tool"] == "get_time" and rows[0]["ok"] is True and rows[0]["origin"] == "model"
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


def _reg(tmp_path, fake):
    from jarves.skills import SkillRegistry
    from jarves.skills.builtin import ClockSkill
    reg = SkillRegistry()
    reg.register(ClockSkill())
    log, _ = mk(tmp_path)
    reg.receipts = log
    reg._call = fake
    return reg


def test_unknown_model_tool_names_never_reach_the_file(tmp_path):
    from jarves.skills import ToolResult

    async def fake_call(name, raw_args=None, *, origin="model"):
        return ToolResult(False, "no such tool", code="UNKNOWN_TOOL")
    reg = _reg(tmp_path, fake_call)
    for bad in ("hunter2_password", "user.name:0541234567"):
        asyncio.run(reg.call(bad, {}))
    text = (tmp_path / ar.RECEIPT_FILE).read_text(encoding="utf-8")
    assert "hunter2" not in text and "0541234567" not in text
    assert [r["tool"] for r in lines(tmp_path)] == ["unknown_tool", "unknown_tool"]


def test_cancelled_call_leaves_a_receipt(tmp_path):
    async def fake_call(name, raw_args=None, *, origin="model"):
        raise asyncio.CancelledError
    reg = _reg(tmp_path, fake_call)
    try:
        asyncio.run(reg.call("get_time", {}))
    except asyncio.CancelledError:
        pass
    rows = lines(tmp_path)
    assert len(rows) == 1 and rows[0]["ok"] is False and rows[0]["code"] == "CANCELLED"


def test_attach_wires_registry_and_follows_feature_state(tmp_path):
    from jarves.skills import SkillRegistry
    state = {"on": False}
    reg = SkillRegistry()
    log = ar.attach(reg, types.SimpleNamespace(active=lambda f: state["on"]), tmp_path)
    assert reg.receipts is log
    assert log.record("clock", True) is None
    state["on"] = True
    assert log.record("clock", True)["tool"] == "clock"


def test_clear_removes_rotated_file_and_reports_leftovers(tmp_path, capsys):
    ns = types.SimpleNamespace(limit=20, json=False, action="clear")
    d = tmp_path / "receipts"
    d.mkdir()
    (d / "tool_receipts.jsonl").write_text("{}\n", encoding="utf-8")
    (d / "tool_receipts.1.jsonl").write_text("{}\n", encoding="utf-8")
    assert ar.cmd_receipts(ns, None, tmp_path) == 0
    assert not list(d.glob("tool_receipts*"))
    # a leftover that cannot be removed: no success message, non-zero exit
    (d / "tool_receipts.1.jsonl").write_text("{}\n", encoding="utf-8")
    capsys.readouterr()
    import pathlib
    orig = pathlib.Path.unlink
    pathlib.Path.unlink = lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("locked"))
    try:
        assert ar.cmd_receipts(ns, None, tmp_path) == 1
    finally:
        pathlib.Path.unlink = orig
    out = capsys.readouterr().out
    assert "cleared" not in out and "tool_receipts.1.jsonl" in out


def test_read_tolerates_corrupt_file(tmp_path):
    d = tmp_path / "receipts"
    d.mkdir()
    (d / "tool_receipts.jsonl").write_bytes(b'{"seq": 1, "tool": "clock"}\nnot json\n[1,2]\n\xff\xfe\n')
    assert ar.read(tmp_path) == [{"seq": 1, "tool": "clock"}] or ar.read(tmp_path) == []
    (d / "tool_receipts.jsonl").write_bytes(b'{"seq": 1, "tool": "clock"}\nnot json\n[1,2]\n')
    assert ar.read(tmp_path) == [{"seq": 1, "tool": "clock"}]
