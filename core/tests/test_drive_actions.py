"""Drive Actions (V6 item 10, issue #217): tap-only links. The server only builds a link; a refused action never claims
success; both switches and the drive scope are required; the Gate and drive mode are untouched."""
import json
import re
from pathlib import Path

import pytest
from test_mobile_drive import Rig, call, MOBILE_DIR  # noqa: F401

from jarves import drive_actions, mobile
from jarves.features import Features
from jarves.config import Config


@pytest.fixture
def rig(home):
    r = Rig(home)
    r.features.set("drive_actions", True, by="cli")
    yield r
    r.d.stop()


def test_call_builds_a_tel_link_only(rig):
    got = drive_actions.build("call", "054-123-4567")
    assert got["ok"] and got["links"] == [{"id": "tel", "url": "tel:+972541234567"}]
    assert "Nothing has happened" in got["note"]


@pytest.mark.parametrize("text", ["112", "100", "police", "משטרה", "abc", "12", "+972 1", "054-123-4567; rm"])
def test_refused_call_has_no_link_and_never_claims_success(text):
    got = drive_actions.build("call", text)
    assert got["ok"] is False and got["code"].startswith("ACTION_") and "links" not in got
    assert "ready" not in got["error"].lower() and got["error_he"]


def test_navigate_and_music_links_are_whitelisted_and_quoted():
    nav = drive_actions.build("navigate", "Ben Gurion Airport & more")
    assert nav["ok"] and [l["id"] for l in nav["links"]] == ["maps", "waze"]
    assert all(l["url"].startswith(drive_actions.LINK_PREFIXES) for l in nav["links"])
    assert "origin=" not in nav["links"][0]["url"] and " " not in nav["links"][0]["url"]
    mus = drive_actions.build("music", "Shalom Hanoch/\"x\"")
    assert mus["ok"] and mus["links"][0]["url"].startswith("https://music.youtube.com/search?q=") and '"' not in mus["links"][0]["url"]
    assert drive_actions.build("navigate", "my location")["ok"] is False             # no location access
    assert drive_actions.build("music", "x" * 500)["code"] == "ACTION_BAD_TEXT"
    assert drive_actions.build("sms", "hi")["code"] == "ACTION_BAD_KIND"            # no messages, no reminders from here


def test_route_needs_scope_and_both_switches(rig):
    assert call(rig.d, "POST", "/api/m/drive/action", None, {"kind": "call", "text": "0541234567"})[0] == 401
    tok0, _ = rig.pair(scopes=("chat", "status"))
    assert call(rig.d, "POST", "/api/m/drive/action", tok0, {"kind": "call", "text": "0541234567"})[0] == 403
    tok, _ = rig.pair()
    st, v = call(rig.d, "POST", "/api/m/drive/action", tok, {"kind": "call", "text": "0541234567"})
    assert st == 200 and v["links"][0]["url"] == "tel:+972541234567"
    assert call(rig.d, "GET", "/api/m/drive", tok)[1]["actions_on"] is True
    rig.features.set("drive_actions", False, by="cli")
    st, v = call(rig.d, "POST", "/api/m/drive/action", tok, {"kind": "call", "text": "0541234567"})
    assert st == 409 and v["code"] == "ACTIONS_OFF" and "links" not in v
    rig.features.set("drive_actions", True, by="cli")
    rig.features.set("phone_drive", False, by="cli")
    assert call(rig.d, "POST", "/api/m/drive/action", tok, {"kind": "call", "text": "0541234567"})[1]["code"] == "DRIVE_SCREEN_OFF"


def test_default_is_off_and_refusal_is_400_without_a_link(rig, home):
    from jarves import features as feat
    assert feat.CATALOG["drive_actions"].default_enabled is False
    tok, _ = rig.pair()
    st, v = call(rig.d, "POST", "/api/m/drive/action", tok, {"kind": "call", "text": "112"})
    assert st == 400 and v["ok"] is False and "links" not in v


def test_text_is_not_stored_or_audited(rig, home):
    tok, _ = rig.pair()
    call(rig.d, "POST", "/api/m/drive/action", tok, {"kind": "call", "text": "0541234567"})
    for p in Path(home).rglob("*"):
        if p.is_file() and p.suffix in (".log", ".jsonl", ".txt", ".json"):
            raw = p.read_text("utf-8", "ignore")
            assert "541234567" not in raw, p


def test_module_has_no_side_effect_imports():
    src = Path(drive_actions.__file__).read_text("utf-8")
    imports = " ".join(x for x in src.splitlines() if re.match(r"\s*(from|import) ", x))
    assert not re.search(r"subprocess|webbrowser|startfile|approvals|skills|httpx|requests|os", imports)


def test_js_only_renders_whitelisted_links_and_has_en_he_parity():
    js = (MOBILE_DIR / "drive.js").read_text("utf-8")
    assert "ACT_LINKS" in js and '"tel:+"' in js
    en = set(re.findall(r"(\w+): \"", js.split("he: {")[0]))
    he = set(re.findall(r"(\w+): \"", js.split("he: {")[1].split("const LINKS")[0]))
    assert en == he
