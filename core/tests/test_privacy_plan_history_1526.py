"""Issue #1526: enabling privacy must erase previously stored plan output."""
from types import SimpleNamespace

from jarves import goal_plans, logs, privacy
from jarves.config import Config
from jarves.userdata import open_db


def test_privacy_switch_scrubs_existing_plan_text(home):
    cfg = Config()
    cfg.log.privacy = False
    config_path = home / "config.toml"
    config_path.write_text("[log]\nprivacy = false\n", encoding="utf-8")
    logs.setup(home / "logs", "INFO", console=False, privacy=False)
    db = open_db(home)
    try:
        plans = goal_plans.PlanStore(db, clock=lambda: 1_800_000_000.0)
        plan = plans.propose("test plan", [{"tool": "clock", "args": {}, "why": "test"}])
        pid = plan["id"]
        db.x("UPDATE goal_plan_steps SET status='failed', evidence=?, error=? WHERE plan_id=?",
             ("private tool output", "private error detail", pid))
        db.x("UPDATE goal_plans SET state='failed', result=?, note=? WHERE id=?",
             ("private result", "private note", pid))
        assert db.row("SELECT evidence FROM goal_plan_steps WHERE plan_id=?", (pid,))["evidence"]
        assert db.row("SELECT result FROM goal_plans WHERE id=?", (pid,))["result"]

        ctx = SimpleNamespace(home=home, cfg=cfg, config_path=config_path)
        assert privacy.set_privacy(ctx, True)["privacy"] is True

        step = db.row("SELECT evidence, error, status FROM goal_plan_steps WHERE plan_id=?", (pid,))
        saved = db.row("SELECT result, note, state FROM goal_plans WHERE id=?", (pid,))
        assert (step["evidence"], step["error"], saved["result"], saved["note"]) == ("", "", "", "")
        assert (step["status"], saved["state"]) == ("failed", "failed")
    finally:
        db.close()
        logs.setup(home / "logs", "INFO", console=False, privacy=True)


async def test_privacy_on_new_steps_keep_status_and_codes_only(tmp_path, monkeypatch):
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
        done, failed = env.store.get(ok["id"]), env.store.get(bad["id"])
        assert done["state"] == "done" and done["steps"][0]["evidence"] == ""
        assert "secret" not in done["result"]
        step = failed["steps"][0]
        assert (failed["state"], step["status"], step["evidence"], step["error"]) == ("failed", "failed", "", "X_BROKE")
        assert "it broke" not in failed["note"]
    finally:
        env.db.close()


def test_full_text_window_off_scrubs_plan_text(home):
    db = open_db(home)
    try:
        pid = goal_plans.PlanStore(db).propose("p", [{"tool": "clock", "args": {}, "why": "t"}])["id"]
        db.x("UPDATE goal_plan_steps SET evidence='private' WHERE plan_id=?", (pid,))
        cfg = Config()
        ctx = SimpleNamespace(home=home, cfg=cfg, config_path=home / "config.toml")
        privacy.set_full_text(ctx, False)
        assert db.row("SELECT evidence FROM goal_plan_steps WHERE plan_id=?", (pid,))["evidence"] == ""
    finally:
        db.close()
        logs.setup(home / "logs", "INFO", console=False, privacy=True)
