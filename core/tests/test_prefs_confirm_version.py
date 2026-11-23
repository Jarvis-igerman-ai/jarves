"""#1514: a preference confirm applies only to the wording it was shown. An edit that commits after the owner saw
wording A, but before their confirm lands, leaves wording B `proposed` (dashboard route and voice tool; the phone op
was already guarded and is covered in test_mobile_prefs.py)."""
from __future__ import annotations

import pytest

from jarves import dashboard
from jarves import preferences as prefs_mod
from jarves.config import Config
from jarves.skills import SkillRegistry
from jarves.userdata import open_db

A, B = "short answers in the morning", "long answers in the morning"


@pytest.fixture
def dash(home):
    db = open_db(home)
    pid = prefs_mod.Preferences(db).suggest(A, "I prefer short answers in the morning", "voice")["item"]["id"]
    db.close()
    ctx = dashboard.Ctx(home, Config())
    yield ctx, prefs_mod._ctx(ctx), pid
    ctx.close()


def test_dashboard_confirm_of_old_wording_after_an_edit_is_refused(dash):
    ctx, p, pid = dash
    p.edit(pid, B, by="phone", repropose=True)                 # the edit commits while the page still shows A
    st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": A})
    assert (st, body["code"]) == (409, "PREF_CONFLICT")
    assert (p.get(pid)["state"], p.get(pid)["text"]) == ("proposed", B)
    st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": B})
    assert st == 200 and p.get(pid)["state"] == "confirmed"


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "  "}, {"text": 7}, {"text": None}])
def test_dashboard_confirm_without_the_shown_text_confirms_nothing(dash, body):
    ctx, p, pid = dash
    st, res = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, **body})
    assert (st, res["code"]) == (400, "PREF_TEXT_REQUIRED")
    assert p.get(pid)["state"] == "proposed"


def test_dashboard_confirm_of_an_item_removed_mid_request_is_not_found(dash, monkeypatch):
    ctx, p, pid = dash
    real = p.confirm
    def removed_then_confirm(*a, **k):
        p.remove(pid)
        return real(*a, **k)
    monkeypatch.setattr(p, "confirm", removed_then_confirm)
    assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": A})[0] == 404


def _voice(p, during_ask=None):
    reg, asked = SkillRegistry(), []
    async def confirm(prompt, **meta):
        asked.append(meta)
        if during_ask:
            during_ask()                                       # the edit lands while the owner reads the popup
        return True
    confirm.accepts_meta = True
    reg.confirm = confirm
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    return reg, asked


async def test_voice_confirm_applies_only_to_the_wording_the_approval_showed(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(A, "I prefer short answers in the morning", "voice")["item"]["id"]
    reg, asked = _voice(p, lambda: p.edit(pid, B, by="phone", repropose=True))
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert asked[0]["details"]["preference"] == A
    assert (r.ok, r.code) == (False, "PREF_CONFLICT")
    assert (p.get(pid)["state"], p.get(pid)["text"]) == ("proposed", B)
    reg, asked = _voice(p)
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert r.ok and asked[0]["details"]["preference"] == B and p.get(pid)["state"] == "confirmed"


async def test_voice_confirm_that_was_never_shown_the_wording_confirms_nothing(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(A, "I prefer short answers in the morning", "voice")["item"]["id"]
    tool = next(t for t in prefs_mod.make_skill(p).tools() if t.name == "pref_confirm")
    r = await tool.handler(tool.args(id=pid))                  # no approval ran, so no wording was shown
    assert (r.ok, r.code) == (False, "PREF_NOT_SHOWN") and p.get(pid)["state"] == "proposed"


async def test_voice_confirm_of_an_item_removed_after_the_approval_is_not_found(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(A, "I prefer short answers in the morning", "voice")["item"]["id"]
    reg, _ = _voice(p, lambda: p.remove(pid))
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert (r.ok, r.code) == (False, "PREF_NOT_FOUND")
