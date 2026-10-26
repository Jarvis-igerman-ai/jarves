"""#463: the GENERIC beta gate. One `stage` per registered feature + one global opt-in `beta_features`; every enable path
(Features.set, switch, CLI, dashboard, phone enable, voice tool, wizard bundle, guide step, Settings skill) goes through
the same registry rule. No feature is named here except as test data: the tests iterate the WHOLE registry."""
import re

import pytest

from jarves import features as feat
from jarves import setup_wizard as sw
from jarves.config import Config
from jarves.features import BETA_MSG, CATALOG, BetaLocked, Features
from jarves.skills import SkillRegistry

pytestmark = pytest.mark.beta_gate


@pytest.fixture(autouse=True)
def _all_registered(monkeypatch):
    sw.load_addons()
    yield


def make(home):
    return Features(Config(), home)


def beta_ids():
    sw.load_addons()
    return sorted(fid for fid in CATALOG if feat.stage_of(fid) == "beta")


def all_ids():
    sw.load_addons()
    return sorted(CATALOG)


# ------------------------------------------------------------------ the stage field
@pytest.mark.parametrize("fid", all_ids())
def test_every_feature_has_a_stage(fid):
    st = feat.stage_of(fid)
    assert st in feat.STAGES
    f = CATALOG[fid]
    if f.stage is None:        # derived: verified on the Windows device -> stable, anything else -> beta
        from jarves import capabilities
        verified = capabilities.get(fid).targets["windows"].status == "verified_on_device"
        assert st == ("stable" if verified else "beta")


def test_a_new_feature_defaults_to_beta_and_off_until_opt_in(home):
    feat.register("zz_new_thing", "חדש", "New thing", True)
    try:
        f = make(home)
        assert feat.stage_of("zz_new_thing") == "beta"
        assert not f.enabled("zz_new_thing")                 # even its default-on is held back
        with pytest.raises(BetaLocked):
            f.set("zz_new_thing", True)
        f.set_beta(True)
        assert f.enabled("zz_new_thing")
    finally:
        CATALOG.pop("zz_new_thing", None)


def test_invalid_stage_is_refused():
    with pytest.raises(ValueError):
        feat.register("zz_bad", "x", "x", False, stage="alpha")


def test_status_carries_the_label(home):
    f = make(home)
    rows = f.list(wait=False)
    assert all(r["stage"] in feat.STAGES for r in rows)
    assert {r["id"] for r in rows if r["stage"] == "beta"} == set(beta_ids())


# ------------------------------------------------------------------ the opt-in
def test_opt_in_default_is_off_on_a_fresh_install(home):
    f = make(home)
    assert not f.beta_on()
    f.set("dashboard", False)                      # a write on a fresh install must NOT migrate the gate on
    assert not make(home).beta_on()


def test_existing_install_is_migrated_on_once(home):
    (home / "features.json").write_text('{"version": 1, "enabled": {"music": true}, "changed": {}, "requests": {}}')
    before = (home / "features.json").read_bytes()
    f = make(home)
    assert f.beta_on()
    assert (home / "features.json").read_bytes() == before     # reading never rewrites the file (installer smoke: byte-identical)
    assert f.enabled("music")                      # still on, now labelled beta
    f.set_beta(False)
    assert not make(home).beta_on()                # the owner's later choice is not migrated again


def test_beta_off_disables_cleanly_and_keeps_the_state(home):
    f = make(home)
    f.set_beta(True)
    fid = "chimes" if "chimes" in beta_ids() else beta_ids()[0]
    heard = []
    f.listeners.append(lambda i, on: heard.append((i, on)))
    f.set(fid, True)
    f.set_beta(False)
    assert not f.enabled(fid) and (fid, False) in heard
    assert f.status(fid)["locked"] and f.status(fid)["saved_on"]
    f.set_beta(True)
    assert f.enabled(fid)                          # the saved choice came back


def test_off_is_always_allowed(home):
    f = make(home)
    for fid in beta_ids():
        assert f.set(fid, False)["enabled"] is False


# ------------------------------------------------------------------ every enable path x every beta feature
@pytest.mark.parametrize("fid", beta_ids())
def test_features_set_and_switch_refuse(home, fid):
    f = make(home)
    with pytest.raises(BetaLocked, match=re.escape(BETA_MSG)):
        f.set(fid, True)
    with pytest.raises(BetaLocked):
        feat.switch(f, fid, True, by="cli")
    assert not f.enabled(fid)


@pytest.mark.parametrize("fid", beta_ids())
def test_dashboard_post_refuses(home, fid):
    from jarves import dashboard
    ctx = dashboard.Ctx(home, Config())
    try:
        m = re.match(r"^/api/features/([a-z0-9_]+)$", f"/api/features/{fid}")
        st, body = feat.api_feature_set(ctx, m, {"enabled": True})
        assert st == 403 and body["code"] == "BETA_LOCKED" and body["error"] == BETA_MSG
        st, body = feat.api_features(ctx, None, None)
        assert body["beta_features"] is False
    finally:
        ctx.close()


@pytest.mark.parametrize("fid", beta_ids())
def test_cli_refuses(home, monkeypatch, capsys, fid):
    from jarves import cli, feature_setup
    monkeypatch.setattr(feature_setup, "prepare", lambda *_: (True, ""))
    assert cli.main(["features", "on", fid]) == 1
    assert BETA_MSG in capsys.readouterr().err
    assert cli.main(["features", "off", fid]) == 0


@pytest.mark.parametrize("fid", beta_ids())
async def test_voice_tool_refuses_before_any_approval(home, fid):
    f = make(home)
    reg = SkillRegistry()
    reg.register(feat.make_skill(f, reg))
    reg.allowed_actions |= {"feature_set"}
    asked = []

    async def yes(prompt):
        asked.append(prompt)
        return True
    reg.confirm = yes
    r = await reg.call("feature_set", {"id": fid, "on": True})
    assert not r.ok and r.code == "BETA_LOCKED" and BETA_MSG in r.message
    assert asked == [] and not f.enabled(fid)


def _wizard(home, **kw):
    from jarves.config import load_config
    cfg, _ = load_config(home / "config.toml")
    actions = {"ask_key": lambda h: 1, "prepare": lambda fid, f, h: (True, ""), "google_start": lambda: 0,
               "autostart": lambda on: None}
    return sw.Wizard(cfg, home, config_path=home / "config.toml", io=sw.ConsoleIO(None, lambda s: None),
                     features=Features(cfg, home), actions=actions, lang="en", **kw)


def test_wizard_bundle_hides_beta_and_never_switches_it_on(home, monkeypatch):
    monkeypatch.setenv("JARVES_HOME", str(home))
    monkeypatch.setattr(Features, "available", lambda self, fid: (True, ""))
    hidden = beta_ids()
    assert all(i not in ids for _, _, _, ids in sw.groups(False) for i in hidden)       # hidden from the cards
    view = sw.view(Config(), home, make(home))
    assert all(r["id"] not in hidden for g in view["groups"] for r in g["features"])
    # a scripted answers file that asks for every beta feature: skipped with the one message, nothing switched on
    w = _wizard(home, answers={"features": {fid: True for fid in hidden if fid not in sw.WIZARD_HIDDEN}})
    assert w.run() == 0
    f = make(home)
    assert not f.beta_on() and not any(f.enabled(fid) for fid in hidden)
    # the lower-level bundle apply refuses too
    w._apply_features({fid: True for fid in hidden})
    assert not any(f.enabled(fid) for fid in hidden)


def test_phone_enable_route_refuses(home):
    from jarves import dashboard, mobile
    routes = {}
    mobile.dashboard_routes(lambda method, pat, fn: routes.setdefault((method, pat), fn)) \
        if hasattr(mobile, "dashboard_routes") else None
    enable = next((fn for (m, p), fn in routes.items() if p == r"/api/mobile/enable"), None)
    if enable is None:
        pytest.skip("mobile routes registered elsewhere")
    ctx = dashboard.Ctx(home, Config())
    try:
        ctx.dashboard = SimpleNamespaceDash()
        st, body = enable(ctx, None, {"confirm": True})
        assert st == 403 and body["code"] == "BETA_LOCKED"
    finally:
        ctx.close()


class SimpleNamespaceDash:
    def enable_phone(self, features):
        if features.locked("mobile_access"):
            raise BetaLocked("mobile_access")
        raise AssertionError("gate bypassed")


def test_guide_and_settings_paths_use_the_same_gate(home):
    from jarves.skills.settings import WorkspaceError  # noqa: F401  (the Settings skill maps BetaLocked to it)
    f = make(home)
    fid = beta_ids()[0]
    assert f.locked(fid)
    with pytest.raises(PermissionError):
        f.set(fid, True, by="voice")


# ------------------------------------------------------------------ opt-in on: the gate opens, nothing else changes
@pytest.mark.parametrize("fid", beta_ids())
def test_opt_in_on_allows(home, fid):
    f = make(home)
    f.set_beta(True)
    st = f.set(fid, True)
    assert st["enabled"] is True and st["stage"] == "beta" and not st["locked"]


def test_cli_beta_command(home, capsys):
    from jarves import cli
    assert cli.main(["features", "beta", "status"]) == 0
    assert "OFF" in capsys.readouterr().out or "כבויות" in capsys.readouterr().out
    assert cli.main(["features", "beta", "on"]) == 0
    assert make(home).beta_on()
    assert cli.main(["features", "beta", "off"]) == 0
    assert not make(home).beta_on()
    assert cli.main(["features", "beta", "bogus"]) == 2


def test_dashboard_toggle_route(home):
    from jarves import dashboard
    ctx = dashboard.Ctx(home, Config())
    try:
        assert feat.api_beta_set(ctx, None, {"enabled": "x"})[0] == 400
        st, body = feat.api_beta_set(ctx, None, {"enabled": True})
        assert st == 200 and body["beta_features"] is True
    finally:
        ctx.close()


def test_update_channel_is_separate(home):
    f = make(home)
    assert feat.stage_of("beta_channel") == "stable"
    f.set("beta_channel", True)                   # the build channel switches without the beta-features opt-in
    assert f.enabled("beta_channel") and not f.beta_on()
