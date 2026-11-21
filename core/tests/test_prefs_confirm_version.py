"""Issue #1514: a preference confirm carries the version (`updated`) of the wording it was shown. An edit that lands
between showing wording A and the confirm must not let that confirm approve wording B, on any path."""
import asyncio
from types import SimpleNamespace

import pytest

from jarves import mobile_prefs, preferences as prefs_mod
from jarves.preferences import PrefChanged, Preferences
from jarves.userdata import open_db


@pytest.fixture
def prefs(home):
    t = [1000.0]                                          # a frozen clock: the edit happens in the same tick as the read
    p = Preferences(open_db(home), clock=lambda: t[0])
    item = p.suggest("Play jazz while cooking", "I love jazz when I cook", "music")["item"]
    return p, item["id"], item["updated"]


def _edit_to_b(p, pid):
    p.edit(pid, "Play loud metal while cooking", by="voice", repropose=True)


def test_store_confirm_with_the_old_version_after_an_edit_stays_proposed(prefs):
    p, pid, shown = prefs
    _edit_to_b(p, pid)
    with pytest.raises(PrefChanged):
        p.confirm(pid, by="phone", shown=shown)
    assert p.get(pid)["state"] == "proposed" and not p.for_prompt()
    r = p.confirm(pid, by="phone", shown=p.get(pid)["updated"])         # the current version: confirmed
    assert r["item"]["state"] == "confirmed" and p.for_prompt() == ["Play loud metal while cooking"]


def test_phone_confirm_needs_the_shown_version(prefs, home):
    p, pid, shown = prefs
    mob = SimpleNamespace(bridge=None, _prefs=p, home=home)
    assert mobile_prefs.action(mob, {"op": "confirm", "pref": pid}, "dev")[0] == 400        # no version: refused
    _edit_to_b(p, pid)
    st, body = mobile_prefs.action(mob, {"op": "confirm", "pref": pid, "updated": shown}, "dev")
    assert st == 409 and body["code"] == "PREF_CHANGED" and p.get(pid)["state"] == "proposed"
    st, body = mobile_prefs.action(mob, {"op": "confirm", "pref": pid, "updated": p.get(pid)["updated"]}, "dev")
    assert st == 200 and body["state"] == "confirmed"


def test_dashboard_confirm_needs_the_shown_version(prefs):
    p, pid, shown = prefs
    ctx = SimpleNamespace(_prefs=p)
    assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "updated": "x"})[0] == 400
    _edit_to_b(p, pid)
    st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "updated": shown})
    assert st == 409 and body["code"] == "PREF_CHANGED" and p.get(pid)["state"] == "proposed"
    assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "updated": p.get(pid)["updated"]})[0] == 200
    assert p.get(pid)["state"] == "confirmed"


def _voice(p, during_popup):
    from jarves.skills import SkillRegistry
    reg = SkillRegistry()

    async def popup(prompt, **meta):                       # the owner sees the wording, then taps yes
        during_popup()
        return True
    popup.accepts_meta = True
    reg.confirm = popup
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    return reg


def test_voice_confirm_after_an_edit_during_the_popup_is_refused(prefs):
    p, pid, _ = prefs
    reg = _voice(p, lambda: _edit_to_b(p, pid))             # wording A in the popup, B committed before the yes
    r = asyncio.run(reg.call("pref_confirm", {"id": pid}, origin="model"))
    assert not r.ok and r.code == "PREF_CHANGED" and p.get(pid)["state"] == "proposed" and not p.for_prompt()
    r = asyncio.run(_voice(p, lambda: None).call("pref_confirm", {"id": pid}, origin="model"))
    assert r.ok and p.get(pid)["state"] == "confirmed"      # a fresh popup shows B: that yes confirms B
