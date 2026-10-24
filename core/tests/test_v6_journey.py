"""V6 item 3 (#210) journey: typed places only, links, freshness labels, free-provider fallback, CLI, catalog row.
No network: providers are fakes or an httpx MockTransport; no location is read."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from jarves import cli, journey as jn
from jarves import features as feat

NOW = datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc)


class FakeProvider:
    name = "Fake"

    def __init__(self, minutes=70, km=95.0, fail=None):
        self.minutes, self.km, self.fail, self.calls = minutes, km, fail, 0

    def estimate(self, origin, destination, client, now):
        self.calls += 1
        if self.fail:
            raise self.fail
        return jn.RouteEstimate(origin, destination, self.km * 1000, self.minutes * 60, self.name, now)


def test_off_by_default():
    assert feat.CATALOG["journey"].default_enabled is False


@pytest.mark.parametrize("place", ["here", "My Location", "כאן", "המיקום שלי", "current location"])
def test_current_location_is_refused(place):
    with pytest.raises(jn.JourneyError) as e:
        jn.clean_place(place)
    assert e.value.code == "JOURNEY_NEED_PLACE"


@pytest.mark.parametrize("place", ["", "   ", "x" * 500, "a\x00b"])
def test_bad_places(place):
    with pytest.raises(jn.JourneyError) as e:
        jn.clean_place(place)
    assert e.value.code == "JOURNEY_BAD_PLACE"


def test_links_are_encoded_and_carry_the_places():
    o, d = "Tel Aviv & more", "חיפה, ישראל"
    m = urlparse(jn.maps_link(o, d))
    q = parse_qs(m.query)
    assert m.netloc == "www.google.com" and q["origin"] == [o] and q["destination"] == [d] and q["travelmode"] == ["driving"]
    w = urlparse(jn.waze_link(d))
    assert w.netloc == "waze.com" and parse_qs(w.query)["q"] == [d] and parse_qs(w.query)["navigate"] == ["yes"]
    assert " " not in jn.maps_link(o, d) and "&more" not in jn.maps_link(o, d).split("origin=")[1].split("&")[0]


def test_plan_with_fake_provider_labels_no_traffic_and_age():
    p = FakeProvider()
    tp = jn.plan("Tel Aviv", "Haifa", providers=[p], now=NOW, depart_at=NOW)
    assert tp.estimate.minutes == 70 and tp.estimate.km == 95.0 and tp.estimate.live_traffic is False
    assert tp.arrive_at == NOW + timedelta(minutes=70)
    en = " ".join(tp.lines(NOW, "en"))
    assert "no live traffic" in en and "Fake" in en and "just now" in en and "08:00" in en and "09:10" in en
    assert "בלי תנועה בזמן אמת" in " ".join(tp.lines(NOW, "he"))
    assert tp.as_dict()["estimate"]["traffic_label"] == "no live traffic"


def test_age_labels():
    t = NOW
    assert jn.age_label(t, t + timedelta(seconds=20)) == "just now"
    assert jn.age_label(t, t + timedelta(minutes=5)) == "5 min ago"
    assert jn.age_label(t, t + timedelta(minutes=40)).startswith("stale")
    assert "ישן" in jn.age_label(t, t + timedelta(minutes=40), "he")


def test_fallback_to_second_provider_then_links_only():
    bad, good = FakeProvider(fail=httpx.ConnectError("down")), FakeProvider(minutes=30)
    tp = jn.plan("A", "B", providers=[bad, good], now=NOW)
    assert tp.estimate.minutes == 30 and bad.calls == 1 and good.calls == 1
    tp = jn.plan("A", "B", providers=[FakeProvider(fail=httpx.ConnectError("x")), FakeProvider(fail=KeyError("routes"))], now=NOW)
    assert tp.estimate is None and "JOURNEY_PROVIDER_DOWN" in tp.notes
    assert tp.maps_url and tp.waze_url and "No time estimate" in tp.lines(NOW)[0]
    assert jn.plan("A", "B", providers=[], now=NOW).estimate is None


def test_unknown_place_is_reported_not_swallowed():
    with pytest.raises(jn.JourneyError) as e:
        jn.plan("A", "B", providers=[FakeProvider(fail=jn.JourneyError("JOURNEY_NOT_FOUND", place="B"))], now=NOW)
    assert e.value.code == "JOURNEY_NOT_FOUND" and "B" in jn.say(e.value.code, "en", **e.value.kw)


def _osrm_transport(seen):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.headers["user-agent"].startswith("JARVES")
        if req.url.path == "/search":
            q = req.url.params["q"]
            if q == "Nowhere":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"lon": "34.78" if q == "A" else "34.99", "lat": "32.08" if q == "A" else "32.79"}])
        assert req.url.path.startswith("/route/v1/driving/34.780000,32.080000;34.990000,32.790000")
        return httpx.Response(200, json={"routes": [{"distance": 95000.0, "duration": 4200.0}]})
    return httpx.MockTransport(handler)


def test_osrm_provider_through_a_mock_transport(monkeypatch):
    monkeypatch.setenv(jn.NOMINATIM_ENV, "http://geo.test")
    monkeypatch.setenv(jn.OSRM_ENV, "http://osrm.test")
    seen: list = []
    with httpx.Client(transport=_osrm_transport(seen)) as c:
        tp = jn.plan("A", "B", client=c, now=NOW)
        assert tp.estimate.minutes == 70 and tp.estimate.provider == jn.OsrmProvider.name and not tp.estimate.live_traffic
        with pytest.raises(jn.JourneyError) as e:
            jn.plan("A", "Nowhere", client=c, now=NOW)
        assert e.value.code == "JOURNEY_NOT_FOUND"
    assert {r.url.host for r in seen} <= {"geo.test", "osrm.test"}


def test_http_error_from_a_provider_falls_back(monkeypatch):
    monkeypatch.setenv(jn.NOMINATIM_ENV, "http://geo.test")
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as c:
        tp = jn.plan("A", "B", client=c, now=NOW)
    assert tp.estimate is None and tp.notes == ["JOURNEY_PROVIDER_DOWN"]


def test_parse_hhmm():
    base = datetime(2026, 10, 25, 9, 0)
    assert jn.parse_hhmm("08:30", base) == datetime(2026, 10, 26, 8, 30)
    assert jn.parse_hhmm("9:15", base) == datetime(2026, 10, 25, 9, 15)
    with pytest.raises(jn.JourneyError):
        jn.parse_hhmm("25:00", base)


def test_no_location_or_phone_api_in_the_module():
    src = open(jn.__file__, encoding="utf-8").read().lower()
    assert "geolocation" not in src and "gps" not in src and "webbrowser" not in src and "os.startfile" not in src


def test_cli_needs_the_switch_then_links_only_makes_no_network_call(home, capsys, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(httpx.Client, "send", boom)
    assert cli.main(["journey", "plan", "--from", "A", "--to", "B", "--links-only"]) == 2
    assert "jarvis features on journey" in capsys.readouterr().err
    assert cli.main(["features", "on", "journey"]) == 0
    capsys.readouterr()
    assert cli.main(["journey", "plan", "--from", "Tel Aviv", "--to", "Haifa", "--links-only", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["estimate"] is None and out["maps_url"].startswith("https://www.google.com/maps/dir/")
    assert cli.main(["journey", "plan", "--from", "here", "--to", "Haifa", "--links-only"]) == 1
    assert "do not read" in capsys.readouterr().err
    assert cli.main(["journey", "plan", "--from", "A", "--to", "B", "--links-only", "--depart", "99:99"]) == 1


def test_catalog_row_is_honest():
    f = feat.CATALOG["journey"]
    assert f.targets["windows"]["status"] == "implemented_unverified" and f.risk == "low" and not f.skills and not f.tools
