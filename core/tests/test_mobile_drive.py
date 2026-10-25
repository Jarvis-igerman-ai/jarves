"""Phone Drive tab (V6 item 11, issue #218): the `drive` scope (off by default), read-only drive mode with the queue COUNT
and tool names only, the passenger question answered by the phone without touching the saved setting, a typed trip plan
with Maps / Waze links, privacy (no text in the audit or the ledger), and the static files (CSP-safe, EN = HE)."""
import http.client
import json
import re
from pathlib import Path

import pytest

from jarves import dashboard, drive_briefing, drive_mode, journey, logs, mobile, mobile_drive  # noqa: F401
from jarves.config import Config
from jarves.features import Features

HOST = "192.168.50.7"
MOBILE_DIR = Path(mobile.__file__).parent / "dashboard_static" / "mobile"


def call(d, method, path, token=None, body=None, owner=False):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
    h = {"Host": f"127.0.0.1:{d.port}" if owner else f"{HOST}:{d.port}"}
    if owner:
        h["Cookie"] = f"{d.access.cookie}={d.url.split('t=')[1]}"
    if method != "GET":
        h["X-Jarves"] = "1"
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw or b"{}")
    except ValueError:
        return r.status, {"text": raw.decode("utf-8", "replace")}


class FakeProvider:
    name = "fake-osrm"

    def estimate(self, origin, destination, client, now):
        return journey.RouteEstimate(origin, destination, 12_300, 1_260, self.name, now, False)


@pytest.fixture(autouse=True)
def fake_route(monkeypatch):
    real = journey.plan
    monkeypatch.setattr(journey, "plan", lambda o, d, **kw: real(o, d, providers=[FakeProvider()], **kw))


class Rig:
    def __init__(self, home):
        from conftest import free_port
        from jarves.mobile import Bridge
        self.home, self.calls, self.setting = home, [], "ask"
        self.features = Features(Config(), home)
        self.features.set("journey", True, by="cli")
        self.features.set("drive_briefing", True, by="cli")

        def briefing(mode):
            self.calls.append(mode)
            return {"ok": True, "text": "x", "sections": [
                {"id": "weather", "ok": True, "text": "Sunny, 24 degrees.", "untrusted_title": "SECRET TITLE", "place": "SECRET PLACE"}]}
        b = Bridge(chat=lambda t, d: {"reply": "x", "ok": True}, voice=lambda w, d: {},
                   status=lambda: {"core": "running", "components": {}},
                   drive_features=lambda: self.features, drive_passenger=lambda: self.setting, drive_briefing=briefing)
        self.d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=b).start()

    def pair(self, scopes=("chat", "status", "drive")):
        st, code = call(self.d, "POST", "/api/mobile/code", body={}, owner=True)
        st, got = call(self.d, "POST", "/api/m/pair", body={"code": code["code"], "name": "Galaxy"})
        assert st == 200
        assert call(self.d, "POST", f"/api/mobile/devices/{got['device']}/scopes", body={"scopes": list(scopes)}, owner=True)[0] == 200
        return got["token"], got["device"]


@pytest.fixture
def rig(home):
    r = Rig(home)
    yield r
    r.d.stop()


def test_the_tab_needs_the_drive_scope_and_a_token(rig):
    for method, path in (("GET", "/api/m/drive"), ("GET", "/api/m/drive/briefing?passenger=alone"), ("POST", "/api/m/drive/journey")):
        assert call(rig.d, method, path, None, {} if method == "POST" else None)[0] == 401          # no unauthenticated route
    assert "drive" in mobile.ALL_SCOPES and "drive" not in mobile.DEFAULT_SCOPES
    tok, _ = rig.pair(scopes=("chat", "status", "approvals", "voice"))                              # default scopes only
    assert call(rig.d, "GET", "/api/m/drive", tok)[0] == 403
    assert call(rig.d, "GET", "/api/m/drive/briefing?passenger=alone", tok)[0] == 403
    assert call(rig.d, "POST", "/api/m/drive/journey", tok, {"origin": "A", "destination": "B"})[0] == 403
    assert rig.calls == []
    tok, _ = rig.pair()
    assert call(rig.d, "GET", "/api/m/drive", tok)[0] == 200


def test_drive_mode_is_shown_read_only_with_tool_names_and_a_count(rig, home):
    tok, _ = rig.pair()
    st, v = call(rig.d, "GET", "/api/m/drive", tok)
    assert st == 200 and v["drive_mode"] is False and v["waiting"] == 0 and v["queue"] == [] and v["enabled"] is True
    rig.features.set("drive_mode", True, by="cli")
    q = drive_mode.queue_for(home)
    q.add("message_send", "whatsapp_send -> Dana", source="owner")
    q.add("long_read", "gmail_read my private bank statement text", source="owner")
    _, v = call(rig.d, "GET", "/api/m/drive", tok)
    assert v["drive_mode"] is True and v["waiting"] == 2
    assert v["queue"] == [{"tool": "whatsapp_send"}, {"tool": "gmail_read"}]
    raw = json.dumps(v)
    assert "Dana" not in raw and "bank" not in raw                                                    # never text, never a name


def test_the_phone_cannot_switch_drive_mode_or_do_anything_from_this_screen(rig):
    tok, _ = rig.pair()
    for method, path in (("POST", "/api/m/drive"), ("PUT", "/api/m/drive"), ("POST", "/api/m/drive/on"), ("POST", "/api/m/drive/off"),
                         ("POST", "/api/m/drive/approve"), ("POST", "/api/m/drive/call"), ("POST", "/api/m/drive/message"),
                         ("POST", "/api/m/drive/stopped"), ("POST", "/api/m/drive/briefing")):
        st, _ = call(rig.d, method, path, tok, {"on": True} if method != "GET" else None)
        assert st in (404, 405), (method, path, st)
    assert call(rig.d, "POST", "/api/m/features/drive_mode", tok, {"enabled": True})[0] == 403      # the Features page stays PC-only
    assert call(rig.d, "POST", "/api/features/drive_mode", tok, {"enabled": True})[0] == 403
    assert Features(Config(), rig.home).enabled("drive_mode") is False


@pytest.mark.parametrize("setting,answer,mode,held", [
    ("ask", "alone", "alone", False), ("ask", "present", "present", False),
    ("present", "alone", "present", True), ("present", "present", "present", False),
    ("alone", "present", "present", False), ("alone", "alone", "alone", False)])
def test_the_phone_answers_the_passenger_question_but_never_less_private_than_the_setting(rig, setting, answer, mode, held):
    rig.setting = setting
    tok, _ = rig.pair()
    st, b = call(rig.d, "GET", f"/api/m/drive/briefing?passenger={answer}", tok)
    assert st == 200 and b["mode"] == mode and b["held_by_setting"] is held and rig.calls == [mode]
    assert b["sections"] == [{"id": "weather", "ok": True, "text": "Sunny, 24 degrees."}]            # only id/ok/text pass
    assert "SECRET" not in json.dumps(b)
    assert rig.setting == setting                                                                    # the saved setting is untouched


def test_without_an_answer_it_asks_and_reads_nothing_unless_the_setting_says_so(rig):
    tok, _ = rig.pair()
    st, b = call(rig.d, "GET", "/api/m/drive/briefing", tok)
    assert st == 200 and b == {"ask": True, "mode": "ask"} and rig.calls == []
    assert call(rig.d, "GET", "/api/m/drive/briefing?passenger=everyone", tok)[0] == 400
    rig.setting = "alone"                                                                            # the owner's own persistent choice
    assert call(rig.d, "GET", "/api/m/drive/briefing", tok)[1]["mode"] == "alone" and rig.calls == ["alone"]


def test_effective_mode_is_a_pure_rule():
    for s in ("ask", "weird", None):
        assert mobile_drive.effective_mode(s, None) == "ask"
        assert mobile_drive.effective_mode(s, "present") == "present"
    assert mobile_drive.effective_mode("present", "alone") == "present"
    assert mobile_drive.effective_mode("alone", "present") == "present"
    assert mobile_drive.tool_name("whatsapp_send -> Dana") == "whatsapp_send"
    assert mobile_drive.tool_name("Dana's secret") == "other" and mobile_drive.tool_name("") == "other"


def test_switches_off_on_the_computer_are_honest(rig):
    tok, _ = rig.pair()
    rig.features.set("drive_briefing", False, by="cli")
    st, b = call(rig.d, "GET", "/api/m/drive/briefing?passenger=alone", tok)
    assert st == 409 and b["code"] == "BRIEFING_OFF" and rig.calls == []
    rig.features.set("journey", False, by="cli")
    st, b = call(rig.d, "POST", "/api/m/drive/journey", tok, {"origin": "Tel Aviv", "destination": "Haifa"})
    assert st == 409 and b["code"] == "JOURNEY_OFF"
    rig.features.set("phone_drive", False, by="cli")
    _, v = call(rig.d, "GET", "/api/m/drive", tok)
    assert v["enabled"] is False
    assert call(rig.d, "GET", "/api/m/drive/briefing?passenger=alone", tok)[0] == 409
    assert call(rig.d, "POST", "/api/m/drive/journey", tok, {"origin": "A1", "destination": "B1"})[0] == 409


def test_journey_returns_labelled_estimate_and_link_buttons_only_from_the_two_known_hosts(rig):
    tok, _ = rig.pair()
    st, j = call(rig.d, "POST", "/api/m/drive/journey", tok, {"origin": "Tel Aviv", "destination": "Haifa"})
    assert st == 200
    assert "about 21 min" in j["lines"]["en"][0] and "no live traffic" in j["lines"]["en"][0] and j["lines"]["he"][0] != j["lines"]["en"][0]
    assert j["maps_url"].startswith("https://www.google.com/maps/dir/?") and j["waze_url"].startswith("https://waze.com/ul?")
    assert "Haifa" in j["maps_url"] and j["estimate"]["live_traffic"] is False
    assert not any(k in j for k in ("lat", "lon", "location"))


def test_journey_needs_typed_places_and_never_reads_a_location(rig):
    tok, _ = rig.pair()
    for o, d in (("", "Haifa"), ("Tel Aviv", ""), ("here", "Haifa"), ("Tel Aviv", "my location"), ("x" * 500, "Haifa")):
        st, j = call(rig.d, "POST", "/api/m/drive/journey", tok, {"origin": o, "destination": d})
        assert st == 400 and j["error"] and j["error_he"] and j["code"].startswith("JOURNEY_"), (o, d, j)


def test_journey_is_rate_limited_per_phone(rig):
    tok, _ = rig.pair()
    sts = [call(rig.d, "POST", "/api/m/drive/journey", tok, {"origin": "A1", "destination": "B1"})[0]
           for _ in range(mobile_drive.JOURNEY_RATE_PER_MIN + 2)]
    assert sts[:mobile_drive.JOURNEY_RATE_PER_MIN] == [200] * mobile_drive.JOURNEY_RATE_PER_MIN and sts[-1] == 429


def test_privacy_mode_keeps_no_place_or_text_in_the_audit_the_ledger_or_the_logs(rig, home, monkeypatch):
    monkeypatch.setattr(logs, "privacy", lambda: True)
    tok, _ = rig.pair()
    _, v = call(rig.d, "GET", "/api/m/drive", tok)
    assert v["privacy"] is True
    call(rig.d, "POST", "/api/m/drive/journey", tok, {"origin": "Hidden Origin Street 7", "destination": "Secret Destination Road 9"})
    call(rig.d, "GET", "/api/m/drive/briefing?passenger=alone", tok)
    audit = (home / mobile.AUDIT_FILE).read_text(encoding="utf-8")
    assert "Hidden Origin" not in audit and "Secret Destination" not in audit and "Sunny" not in audit and '"journey"' in audit
    ledger = rig.d.mobile.cont._rows("SELECT body FROM ledger")
    assert not any("Hidden" in r["body"] or "Secret" in r["body"] for r in ledger)


def test_a_dashboard_route_is_still_closed_to_a_phone_token(rig):
    tok, _ = rig.pair()
    assert call(rig.d, "GET", "/api/features", tok)[0] == 403
    assert call(rig.d, "GET", "/api/drive", tok)[0] == 403


def test_the_owner_can_grant_and_revoke_the_scope_on_the_pc(rig, home, capsys):
    from jarves import cli
    _, dev = rig.pair(scopes=("chat",))
    assert cli.main(["phone", "scopes", dev, "--grant", "drive"]) == 0
    assert "drive" in capsys.readouterr().out and "drive" in mobile.MobileAccess(home).devices()[0]["scopes"]
    assert cli.main(["phone", "scopes", dev, "--revoke", "drive"]) == 0
    assert "drive" not in mobile.MobileAccess(home).devices()[0]["scopes"]


def test_the_row_is_registered_and_honest():
    from jarves import capabilities as cap
    from jarves import features
    assert "phone_drive" in features.CATALOG and features.CATALOG["phone_drive"].default_enabled is True
    v = cap.view("phone_drive")
    assert v["problems"] == [] and v["targets"]["phone"]["blocker"].startswith("NOT-RUN") and v["targets"]["car"]["blocker"].startswith("NOT-RUN")
    assert all(v["targets"][t]["status"] != "verified_on_device" for t in ("phone", "car"))


# ---------------------------------------------------------------- static files
def test_drive_js_is_served_csp_safe_translated_and_never_opens_a_link_itself():
    js = (MOBILE_DIR / "drive.js").read_text(encoding="utf-8")
    assert "drive.js" in mobile.STATIC_FILES and "innerHTML" not in js and "eval(" not in js and "Authorization" not in js
    assert not re.search(r"window\.open|location\.(href|assign|replace)|\.click\(\)|navigator\.geolocation", js)    # a link is the owner's tap
    assert "rel = \"noopener noreferrer\"" in js

    def body(lang):
        m = re.search(rf"\n    {lang}: \{{(.*?)(?=\n    (?:he|en): \{{|\n  \}};)", js, re.S)
        return set(re.findall(r"\b([a-z_]+):\s*\"", m.group(1)))
    en, he = body("en"), body("he")
    assert en == he and len(en) > 25
    used = set(re.findall(r'\bt\("([a-z_]+)"\)', js)) | {"maps", "waze", "from", "to"}
    assert used <= en
    assert re.search(r"[֐-׿]", js)
    html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
    assert '/m/drive.js' in html and 'id="driveRoot"' in html and 'id="tabbtn-drive"' in html
    assert re.search(r'id="tabbtn-drive"[^>]*\bhidden\b', html)                                      # hidden by default
    app = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
    assert 'drive: "scope:drive"' in app and "tab_drive" in app


def test_trip_limit_is_shared_across_all_phones(rig):
    toks = [rig.pair()[0] for _ in range(3)]
    sts = []
    for i in range(mobile_drive.JOURNEY_ALL_PHONES_PER_MIN + 2):          # 3 phones x 4 taps stays under each phone's own 6/min
        sts.append(call(rig.d, "POST", "/api/m/drive/journey", toks[i % 3], {"origin": "A1", "destination": "B1"}))
    ok = [s for s, _ in sts if s == 200]
    assert len(ok) == mobile_drive.JOURNEY_ALL_PHONES_PER_MIN
    st, j = sts[-1]
    assert st == 429 and j["error"] and j["error_he"] and j["code"] == "JOURNEY_BUSY"


def test_briefing_has_its_own_rate_limit_per_phone_with_en_he_message(rig):
    tok, _ = rig.pair()
    sts = [call(rig.d, "GET", "/api/m/drive/briefing?passenger=alone", tok) for _ in range(mobile_drive.BRIEFING_RATE_PER_MIN + 2)]
    assert [s for s, _ in sts[:mobile_drive.BRIEFING_RATE_PER_MIN]] == [200] * mobile_drive.BRIEFING_RATE_PER_MIN
    st, j = sts[-1]
    assert st == 429 and j["error"] and j["error_he"] and j["code"] == "DRIVE_RATE_LIMITED"
    assert len(rig.calls) == mobile_drive.BRIEFING_RATE_PER_MIN                    # the limited taps never reached the tool
    tok2, _ = rig.pair()                                                           # another phone has its own bucket
    assert call(rig.d, "GET", "/api/m/drive/briefing?passenger=alone", tok2)[0] == 200


def test_journey_limit_message_is_in_both_languages(rig):
    tok, _ = rig.pair()
    last = None
    for _ in range(mobile_drive.JOURNEY_RATE_PER_MIN + 1):
        last = call(rig.d, "POST", "/api/m/drive/journey", tok, {"origin": "A1", "destination": "B1"})
    assert last[0] == 429 and last[1]["error_he"] and last[1]["error"]


def test_clean_place_strips_bidi_and_invisible_format_characters_like_the_queue():
    assert journey.clean_place("Tel\u202e Aviv\u200b\u2066") == "Tel Aviv"
    assert journey.clean_place("\ufeffHai\u00adfa") == "Haifa"
    assert drive_mode.strip_invisible("a\u202eb\u2069c\u200d") == "abc"
    with pytest.raises(journey.JourneyError):
        journey.clean_place("\u202e\u200b")                                       # nothing left once invisibles are gone
    with pytest.raises(journey.JourneyError):
        journey.clean_place("Haifa\x07")                                          # control characters are still refused
