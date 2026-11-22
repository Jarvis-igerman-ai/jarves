"""Issue #1526: enabling privacy must erase previously stored plan output; codes and states stay."""
import time
from types import SimpleNamespace

import pytest

from jarves import goal_plans, logs, privacy
from jarves.config import Config
from jarves.userdata import open_db


@pytest.fixture(autouse=True)
def _logs_restored(monkeypatch):
    """The privacy switch and the full-text window live in logs module globals: restore them after each test."""
    for name in ("_privacy", "_until", "_clock"):
        monkeypatch.setattr(logs, name, getattr(logs, name))


def _ctx(home, private: bool):
    cfg = Config()
    cfg.log.privacy = private
    (home / "config.toml").write_text(f"[log]\nprivacy = {str(private).lower()}\n", encoding="utf-8")
    return SimpleNamespace(home=home, cfg=cfg, config_path=home / "config.toml")


def _seed(db) -> int:
    """One failed plan with free text and a code, written while privacy was off."""
    pid = goal_plans.PlanStore(db, clock=lambda: 1_800_000_000.0).propose(
        "p", [{"tool": "clock", "args": {}, "why": "t"}, {"tool": "clock", "args": {}, "why": "t"}])["id"]
    db.x("UPDATE goal_plan_steps SET status='failed', evidence=?, error=? WHERE plan_id=? AND idx=0",
         ("private tool output", "X_BROKE: private error detail", pid))
    db.x("UPDATE goal_plan_steps SET status='blocked', error='private reason' WHERE plan_id=? AND idx=1", (pid,))
    db.x("UPDATE goal_plans SET state='failed', result='private result', note='step 1: X_BROKE: private note' "
         "WHERE id=?", (pid,))
    return pid


def _texts(db, pid):
    s = [(r["status"], r["evidence"], r["error"]) for r in
         db.rows("SELECT status, evidence, error FROM goal_plan_steps WHERE plan_id=? ORDER BY idx", (pid,))]
    p = db.row("SELECT state, result, note FROM goal_plans WHERE id=?", (pid,))
    return s, (p["state"], p["result"], p["note"])


SCRUBBED = ([("failed", "", "X_BROKE"), ("blocked", "", "")], ("failed", "", "step 1: X_BROKE"))


def test_privacy_switch_scrubs_existing_plan_text_and_keeps_codes(home):
    logs.setup(home / "logs", "INFO", console=False, privacy=False)
    db = open_db(home)
    try:
        pid = _seed(db)
        assert privacy.set_privacy(_ctx(home, False), True)["privacy"] is True
        assert _texts(db, pid) == SCRUBBED
        assert goal_plans.scrub_private_text(db) == 0          # idempotent: the kept codes survive a second scrub
        assert _texts(db, pid) == SCRUBBED
    finally:
        db.close()


def test_set_privacy_on_when_already_on_does_not_scrub(home, monkeypatch):
    logs.setup(home / "logs", "INFO", console=False, privacy=True)
    calls = []
    monkeypatch.setattr(privacy, "scrub_plans", lambda h: calls.append(h) or 0)
    ctx = _ctx(home, True)
    privacy.set_privacy(ctx, True)
    privacy.set_full_text(ctx, False)
    assert calls == []
    privacy.set_full_text(ctx, True)                            # the window opens, then the owner ends it: one scrub
    privacy.set_full_text(ctx, False)
    assert calls == [home]


async def test_privacy_on_new_steps_keep_status_and_codes_only_and_survive_a_scrub(tmp_path, monkeypatch):
    from test_v5_goal_plans import Env, steps
    monkeypatch.setattr(logs, "privacy", lambda: True)
    env = Env(tmp_path)
    try:
        ok = env.store.propose("ok", steps(("fake_read", {"text": "secret words"})))
        env.store.approve(ok["id"])
        bad = env.store.propose("bad", steps(("fake_bad", {})))
        env.store.approve(bad["id"])
        while await env.runner.tick():
            pass
        for _ in range(2):                                       # as run, then after a Core-start scrub
            done, failed = env.store.get(ok["id"]), env.store.get(bad["id"])
            assert (done["state"], done["steps"][0]["evidence"]) == ("done", "")
            assert done["result"] in ("1/1 steps done. 1. fake_read: done", "")
            step = failed["steps"][0]
            assert (failed["state"], step["status"], step["evidence"], step["error"]) == ("failed", "failed", "", "X_BROKE")
            assert failed["note"] == "step 1: X_BROKE"
            goal_plans.scrub_private_text(env.db)
    finally:
        env.db.close()


async def test_end_of_full_text_window_by_itself_scrubs_plan_text_at_the_next_tick(home):
    from test_v5_goal_plans import Env, steps
    logs.setup(home / "logs", "INFO", console=False, privacy=True)
    now = [time.time()]
    logs._clock = lambda: now[0]
    env = Env(home)
    try:
        privacy.set_full_text(_ctx(home, True), True)
        until = logs.full_text_until()
        assert until and logs.privacy() is False
        p = env.store.propose("p", steps(("fake_read", {"text": "secret words"})))
        env.store.approve(p["id"])
        await env.runner.tick()                                  # runs inside the window: the text is kept
        assert env.store.get(p["id"])["steps"][0]["evidence"] == "read secret words"
        now[0] = until + 1                                       # the window ends; nobody calls set_full_text
        await env.runner.tick()
        d = env.store.get(p["id"])
        assert logs.privacy() is True and (d["steps"][0]["evidence"], d["result"]) == ("", "")
    finally:
        env.db.close()


async def test_core_start_with_privacy_on_scrubs_at_the_first_tick_even_with_plans_off(home, monkeypatch):
    from test_v5_goal_plans import Env
    monkeypatch.setattr(logs, "privacy", lambda: True)
    env = Env(home)
    try:
        pid = _seed(env.db)
        off = SimpleNamespace(active=lambda name: False)
        runner = goal_plans.PlanRunner(env.store, env.reg, clock=env.clock, features=off)   # as Operations builds it
        assert await runner.tick() == 0
        assert _texts(env.db, pid) == SCRUBBED
    finally:
        env.db.close()
