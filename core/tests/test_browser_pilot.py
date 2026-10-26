"""Browser pilot B1 (V7, #396): fake page driver, frozen clock, no browser, no network."""
from __future__ import annotations

import json

import pytest

from jarves import browser_pilot as bp
from jarves import features as feat

SECRET = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


class FakeDriver:
    def __init__(self):
        self.opened, self.closed = [], False

    def open(self, url):
        self.opened.append(url)

    def text(self):
        return "Hello page. key=" + SECRET

    def screenshot(self):
        return b"\x89PNG-fake"

    def close(self):
        self.closed = True


def mk(tmp_path, on=True, **kw):
    t = [1000.0]
    d = FakeDriver()
    return bp.PilotSession(tmp_path, d, enabled=lambda: on, clock=lambda: t[0], **kw), d, t


def audit(tmp_path):
    return [json.loads(x) for x in (tmp_path / bp.AUDIT_FILE).read_text(encoding="utf-8").splitlines()]


def test_switch_off_by_default_and_beta():
    f = feat.FEATURES[bp.FEATURE] if hasattr(feat, "FEATURES") else None
    assert f is None or f.default_enabled is False
    assert feat.stage_of(bp.FEATURE) == "beta"


def test_off_refuses_everything(tmp_path):
    s, d, _ = mk(tmp_path, on=False)
    with pytest.raises(bp.PilotError) as e:
        s.open("http://localhost:8000/")
    assert e.value.code == "BROWSER_PILOT_OFF" and d.opened == []


@pytest.mark.parametrize("url,scope", [
    ("http://localhost:8000/x", "local"), ("http://127.0.0.1/", "local"), ("https://example.com/a", "public"),
    ("http://192.168.1.5/", ""), ("http://10.0.0.1/", ""), ("http://169.254.169.254/", ""), ("file:///c:/x", ""),
    ("javascript:alert(1)", ""), ("https://claude.ai/chats", ""), ("http://printer.local/", ""), ("http://intranet/", "")])
def test_page_scope(url, scope):
    assert bp.page_scope(url) == scope


def test_read_redacts_and_marks_untrusted(tmp_path):
    s, d, _ = mk(tmp_path)
    assert s.open("https://example.com/")["scope"] == "public"
    r = s.read()
    assert SECRET not in r["untrusted_text"] and "Hello page" in r["untrusted_text"]
    assert s.screenshot() == {"bytes": len(b"\x89PNG-fake"), "stored": False}


@pytest.mark.parametrize("kind", ["click", "fill", "submit", "login", "type", "download", "anything"])
def test_no_state_changing_action(tmp_path, kind):
    s, d, _ = mk(tmp_path)
    with pytest.raises(bp.PilotError) as e:
        s.act(kind)
    assert e.value.code == "BROWSER_PILOT_READ_ONLY"
    assert not hasattr(s, "click") and not hasattr(s, "fill")


def test_audit_is_kind_only(tmp_path):
    s, _, _ = mk(tmp_path)
    s.open("https://example.com/secret/path?token=" + SECRET)
    s.read()
    rows = audit(tmp_path)
    assert [r["kind"] for r in rows] == ["open", "read"]
    blob = json.dumps(rows)
    assert "example.com" not in blob and SECRET not in blob and "Hello" not in blob
    assert all(set(r) <= {"seq", "ts", "kind", "result", "state", "title", "details"} for r in rows)


def test_bad_url_refused_and_receipted(tmp_path):
    s, d, _ = mk(tmp_path)
    with pytest.raises(bp.PilotError) as e:
        s.open("http://192.168.0.1/")
    assert e.value.code == "BROWSER_PILOT_URL" and d.opened == []
    assert audit(tmp_path)[-1]["result"] == "BROWSER_PILOT_URL"


def test_state_machine(tmp_path):
    s, d, _ = mk(tmp_path)
    s.pause()
    assert s.state == bp.PAUSED
    with pytest.raises(bp.PilotError) as e:
        s.read()
    assert e.value.code == "BROWSER_PILOT_NOT_AGENT"
    s.resume()
    s.human_takes_over()
    assert s.state == bp.HUMAN
    with pytest.raises(bp.PilotError):
        s.open("https://example.com/")
    s.human_returns()
    assert s.state == bp.AGENT
    s.stop()
    assert s.state == bp.STOPPED and d.closed
    s.resume(); s.human_returns(); s.pause()
    assert s.state == bp.STOPPED
    with pytest.raises(bp.PilotError):
        s.read()


def test_time_cap_stops(tmp_path):
    s, d, t = mk(tmp_path, max_seconds=60)
    t[0] += 61
    with pytest.raises(bp.PilotError) as e:
        s.read()
    assert e.value.code == "BROWSER_PILOT_CAP" and s.state == bp.STOPPED and d.closed


def test_action_cap_stops(tmp_path):
    s, d, _ = mk(tmp_path, max_actions=3)
    s.open("https://example.com/")
    s.read()
    s.read()
    with pytest.raises(bp.PilotError) as e:
        s.read()
    assert e.value.code == "BROWSER_PILOT_CAP" and s.state == bp.STOPPED
