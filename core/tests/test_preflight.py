"""SW-V4 O (#17): preflight, the start-up self-check (<= 1.5 s). Each of 12 planted faults is reported on its own
item and degrades only its component; only an unwritable state folder is fatal. Real files, sockets and SQLite in
tmp dirs; every device / disk probe is injected (no sensors)."""
from __future__ import annotations

import json
import socket
import sqlite3
import time
from pathlib import Path

import pytest

from jarves.config import Config
from jarves.diag import preflight as pf
from jarves.health import Health

NOW = time.time()


def healthy(home: Path, **kw) -> pf.Ctx:
    cfg = Config()
    cfg.voice.wake_enabled = False
    return pf.Ctx(home=home, cfg=cfg, config_path=None, features=None, clock=lambda: NOW,
                  ports=[], find_godot=lambda: Path("godot.exe"), audio_devices=lambda: (1, 1),
                  disk_free_gb=lambda p: 100.0, env={"OPENAI_API_KEY": "present"},
                  previous={"core": "stopped", "ts": NOW - 60}, **kw)


def statuses(res) -> dict[str, str]:
    return {i["id"]: i["status"] for i in res["items"]}


def test_healthy_start_is_green_and_fast(home):
    res = pf.run(healthy(home))
    bad = {k: v for k, v in statuses(res).items() if v not in ("green", "skipped")}
    assert not bad and res["ok"] and not res["fatal"]
    assert res["ms"] <= pf.BUDGET_S * 1000 and res["within_budget"]


def _taken_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


FAULTS = ["godot", "audio", "ports", "state_dir", "wake_model", "config", "secrets", "google_token", "schema",
          "shutdown", "disk", "clock"]


@pytest.mark.parametrize("fault", FAULTS)
def test_each_planted_fault_is_reported_on_its_item_only(home, tmp_path, fault):
    c = healthy(home)
    sock = None
    expect = "red"
    if fault == "godot":
        c.find_godot = lambda: None
    elif fault == "audio":
        c.audio_devices = lambda: (0, 2)                               # no microphone
    elif fault == "ports":
        sock, port = _taken_port()
        c.ports = [("127.0.0.1", port, "link")]
    elif fault == "state_dir":
        f = tmp_path / "a-file"
        f.write_text("x")
        c.home = f                                                     # cannot be a folder: not writable
    elif fault == "wake_model":
        c.cfg.voice.wake_enabled = True
        c.cfg.voice.wake_word = "wake/missing.npz"
    elif fault == "config":
        p = home / "config.toml"
        p.write_text("[voice\nmode = = 'x'\n", encoding="utf-8")
        c.config_path = p
    elif fault == "secrets":
        c.env = {}
        c.cfg.ai.provider, c.cfg.ai.connection = "openai", "api_key"
    elif fault == "google_token":
        (home / "google").mkdir()
        (home / "google" / "token.json").write_text(json.dumps({"refresh_token": "r", "connected_at": NOW - 6 * 86400}))
        expect = "amber"                                               # expires within 48 h
    elif fault == "schema":
        con = sqlite3.connect(home / "store.db")
        con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started REAL)")    # an old, narrower table
        con.commit()
        con.close()
        expect = "amber"
    elif fault == "shutdown":
        c.previous = {"core": "ready", "ts": NOW - 60}
        expect = "amber"
    elif fault == "disk":
        c.disk_free_gb = lambda p: 0.4
    elif fault == "clock":
        c.previous = {"core": "stopped", "ts": NOW + 3600}             # the clock went back an hour
    try:
        res = pf.run(c)
    finally:
        if sock is not None:
            sock.close()
    st = statuses(res)
    assert st[fault] == expect, (fault, res["items"])
    others = {k: v for k, v in st.items() if k != fault and v not in ("green", "skipped")}
    assert not others, (fault, others)
    assert res["fatal"] == (fault == "state_dir")
    assert res["ms"] <= pf.BUDGET_S * 1000
    item = next(i for i in res["items"] if i["id"] == fault)
    assert item["detail"] and (item["fix"] or expect == "amber")
    if fault == "secrets":
        assert "present" not in item["detail"] and "OPENAI_API_KEY" in item["detail"]   # the name, never a value


def test_a_red_item_degrades_only_its_component():
    h = Health()
    for name in ("audio", "ai", "wake_word", "face_link"):
        h.set(name, "ok", "fine")
    res = {"items": [{"id": "audio", "status": "red", "component": "audio", "detail": "no microphone", "fix": "connect"},
                     {"id": "shutdown", "status": "amber", "component": "core", "detail": "unclean", "fix": ""},
                     {"id": "features", "status": "red", "component": "features", "detail": "x", "fix": ""}]}
    touched = pf.apply(h, res)
    assert touched == ["audio"]
    assert h.components["audio"].status == "degraded" and "no microphone" in h.components["audio"].detail
    assert all(h.components[n].status == "ok" for n in ("ai", "wake_word", "face_link"))
    assert "features" not in h.components and "core" not in h.components     # never invented


async def test_core_runs_preflight_before_ready_and_stores_it(home, cfg, monkeypatch):
    from conftest import FakeSTT, FakeTTS
    from jarves.app import Core
    from jarves.diag import db
    from jarves.skills.music import FakePlayer
    from jarves.store import Store
    from jarves.voice.audio import SimAudio
    (home / "health.json").write_text(json.dumps({"core": "ready", "ts": time.time() - 30}))    # last run crashed
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    real_preflight = pf.run_core
    attached = []

    def after_attachment(core):
        # No devices: inspect the registry at the actual lifecycle boundary.
        attached.append(set(core.registry.skills))
        assert {"camera", "appearance", "profiles", "self_manage"} <= attached[-1]
        assert core.dev is not None and core.runtime is not None
        return real_preflight(core)

    monkeypatch.setattr(pf, "run_core", after_attachment)
    await c.start()
    try:
        assert len(attached) == 1
        assert c.preflight is not None and c.preflight["within_budget"]
        assert statuses(c.preflight)["shutdown"] == "amber"
        assert statuses(c.preflight)["ports"] == "green"                     # checked before the link opened
        st = Store(home / "store.db")
        try:
            assert pf.latest(st)["items"] and db.rows(st, "SELECT COUNT(*) n FROM diag_preflight")[0]["n"] == 1
        finally:
            st.close()
        assert c.health.core in ("ready", "degraded")
    finally:
        await c.stop()
