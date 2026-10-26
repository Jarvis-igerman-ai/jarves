"""Opt-in phone location while driving (#339): off by default, drive mode required, rounded, RAM only, fails closed, phone token only."""
import http.client
import json
import re
import time
from pathlib import Path

import pytest

from jarves import journey, phone_location as pl
from test_mobile_drive import MOBILE_DIR, Rig, call, fake_route  # noqa: F401  (fake_route: autouse fake route provider)


@pytest.fixture
def rig(home):
    pl.clear()
    r = Rig(home)
    yield r
    r.d.stop()
    pl.clear()


def fix(**kw):
    d = {"lat": 32.08041, "lon": 34.78061, "accuracy": 20, "ts": time.time() * 1000}
    d.update(kw)
    return d


def on(rig, drive=True, loc=True):
    rig.features.set("phone_location", loc, by="cli")
    rig.features.set("drive_mode", drive, by="cli")


def post_raw(rig, tok, body):
    c = http.client.HTTPConnection("127.0.0.1", rig.d.port)
    c.request("POST", "/api/m/drive/location", body=body, headers={
        "Host": f"192.168.50.7:{rig.d.port}", "X-Jarves": "1", "Content-Type": "application/json", "Authorization": "Bearer " + tok})
    return c.getresponse().status


def disk_bytes(home):
    for p in Path(home).rglob("*"):
        if p.is_file() and p.stat().st_size < 5_000_000:
            yield p.read_bytes()


def test_off_by_default_and_in_the_wizard_group():
    from jarves import features, setup_wizard
    assert features.CATALOG["phone_location"].default_enabled is False
    assert "phone_location" in setup_wizard.BUNDLES if hasattr(setup_wizard, "BUNDLES") else True
    assert any("phone_location" in g[3] for g in setup_wizard.GROUPS)


def test_refused_while_switch_or_drive_mode_is_off(rig):
    tok, _ = rig.pair()
    assert call(rig.d, "POST", "/api/m/drive/location", tok, fix())[1]["code"] == "LOCATION_OFF"
    on(rig, drive=False)
    assert call(rig.d, "POST", "/api/m/drive/location", tok, fix())[0] == 409
    on(rig, loc=False)
    assert call(rig.d, "POST", "/api/m/drive/location", tok, fix())[0] == 409
    assert pl._fixes == {}


@pytest.mark.parametrize("bad", [
    {"lat": 91}, {"lat": -91}, {"lon": 181}, {"lat": float("nan")}, {"lon": float("inf")}, {"lat": True}, {"lat": "32.0"},
    {"accuracy": 100000}, {"accuracy": -1}, {"ts": 0}, {"ts": time.time() * 1000 - 600_000}, {"ts": time.time() * 1000 + 600_000}, {"ts": None}])
def test_bad_payloads_are_refused_and_drop_the_old_fix(rig, bad):
    tok, dev = rig.pair()
    on(rig)
    assert call(rig.d, "POST", "/api/m/drive/location", tok, fix())[0] == 200 and dev in pl._fixes
    assert post_raw(rig, tok, json.dumps(fix(**bad))) == 400      # json.dumps writes NaN / Infinity tokens; still refused
    assert dev not in pl._fixes


def test_coordinates_are_rounded_and_never_on_disk(rig, home):
    tok, dev = rig.pair()
    on(rig)
    assert call(rig.d, "POST", "/api/m/drive/location", tok, fix())[0] == 200
    call(rig.d, "POST", "/api/m/drive/location", tok, {"clear": True})
    assert call(rig.d, "POST", "/api/m/drive/location", tok, fix())[0] == 200
    assert pl._fixes[dev][:2] == (32.08, 34.781)
    for raw in disk_bytes(home):                        # not the raw point, not the rounded one: the audit holds the kind only
        assert b"32.08" not in raw and b"34.78" not in raw


def test_spoofed_from_a_non_phone_client_and_missing_scope(rig):
    assert call(rig.d, "POST", "/api/m/drive/location", None, fix())[0] == 401
    assert call(rig.d, "POST", "/api/m/drive/location", None, fix(), owner=True)[0] == 401     # the owner's browser cookie is not a phone
    assert call(rig.d, "POST", "/api/m/drive/where", None, {})[0] == 401
    tok, _ = rig.pair(scopes=("chat", "status"))
    on(rig)
    assert call(rig.d, "POST", "/api/m/drive/location", tok, fix())[0] == 403
    assert pl._fixes == {}


def test_timeout_drive_off_and_clear_drop_the_fix(rig, monkeypatch):
    tok, dev = rig.pair()
    on(rig)
    call(rig.d, "POST", "/api/m/drive/location", tok, fix())
    assert pl.get(rig.features, dev) is not None
    t0 = pl.clock()
    monkeypatch.setattr(pl, "clock", lambda: t0 + pl.TTL_S + 1)
    assert pl.get(rig.features, dev) is None and dev not in pl._fixes
    monkeypatch.setattr(pl, "clock", lambda: t0)
    call(rig.d, "POST", "/api/m/drive/location", tok, fix())
    rig.features.set("drive_mode", False, by="cli")
    assert call(rig.d, "GET", "/api/m/drive", tok)[1]["location_on"] is False and pl._fixes == {}
    on(rig)
    call(rig.d, "POST", "/api/m/drive/location", tok, fix())
    assert call(rig.d, "POST", "/api/m/drive/location", tok, {"clear": True})[0] == 200 and pl._fixes == {}


def test_eta_from_here_uses_the_rounded_point_and_fails_closed(rig, monkeypatch):
    seen = {}
    real = journey.plan

    def spy(o, d, **kw):
        seen.update(kw)
        return real(o, d, **kw)
    monkeypatch.setattr(journey, "plan", spy)
    tok, _ = rig.pair()
    on(rig)
    assert call(rig.d, "POST", "/api/m/drive/journey", tok, {"origin": "here", "destination": "Tel Aviv"})[0] == 400
    assert "origin_fix" not in seen
    call(rig.d, "POST", "/api/m/drive/location", tok, fix())
    st, r = call(rig.d, "POST", "/api/m/drive/journey", tok, {"origin": "here", "destination": "Tel Aviv"})
    assert st == 200 and seen["origin_fix"] == (32.08, 34.781) and "origin=" not in r["maps_url"]
    seen.clear()
    rig.features.set("drive_mode", False, by="cli")
    assert call(rig.d, "POST", "/api/m/drive/journey", tok, {"origin": "here", "destination": "Tel Aviv"})[0] == 400
    assert "origin_fix" not in seen


def test_where_am_i_sends_only_the_rounded_point(rig, monkeypatch):
    import httpx
    got = {}

    def handler(req):
        got["url"] = str(req.url)
        return httpx.Response(200, json={"display_name": "Allenby Street, Tel Aviv"})
    real = httpx.Client
    monkeypatch.setattr(pl.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    tok, _ = rig.pair()
    assert call(rig.d, "POST", "/api/m/drive/where", tok, {})[0] == 409
    on(rig)
    call(rig.d, "POST", "/api/m/drive/location", tok, fix())
    st, r = call(rig.d, "POST", "/api/m/drive/where", tok, {})
    assert st == 200 and r == {"place": "Allenby Street, Tel Aviv"}
    assert "lat=32.080" in got["url"] and "lon=34.781" in got["url"] and "32.08041" not in got["url"]


def test_per_phone_rate_limit(rig):
    tok, _ = rig.pair()
    on(rig)
    codes = [call(rig.d, "POST", "/api/m/drive/location", tok, fix())[0] for _ in range(pl.FIX_RATE_PER_MIN + 2)]
    assert codes[0] == 200 and codes[-1] == 429


def test_no_tool_or_text_turn_can_reach_the_fix():
    src = Path(pl.__file__).read_text(encoding="utf-8")
    assert "registry" not in src and "Tool(" not in src


def test_the_phone_page_has_matching_strings_and_never_stores():
    js = (MOBILE_DIR / "drive.js").read_text(encoding="utf-8")
    assert "localStorage" not in js and "sessionStorage" not in js and "geolocation.watchPosition" in js
    body = js.split("const LINKS", 1)[0]
    en, he = body.split("    en: {", 1)[1].split("    he: {", 1)
    keys = {"en": set(re.findall(r"(\w+): \"", en)), "he": set(re.findall(r"(\w+): \"", he))}
    assert keys["en"] == keys["he"] and "locshare" in keys["en"]
