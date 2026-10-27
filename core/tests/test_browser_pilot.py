"""Browser pilot B1 (V7, #396): fake page driver, frozen clock, no browser, no network."""
from __future__ import annotations

import json

import pytest

from jarves import browser_pilot as bp
from jarves import features as feat

SECRET = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


class FakeDriver:
    """current_url is a PROPERTY like Playwright page.url and Selenium driver.current_url (#566)."""

    def __init__(self):
        self.opened, self.closed, self.final, self.pinned = [], False, None, []

    @property
    def current_url(self):
        return self.final if self.final is not None else (self.opened[-1] if self.opened else "")

    def pin_hosts(self, mapping):
        self.pinned.append(mapping)

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
    kw.setdefault("resolver", lambda host: ["93.184.216.34"])          # hermetic: no real DNS
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


@pytest.mark.parametrize("url,scope", [
    # numeric hosts a browser reads as IPv4 (octal, hex, shortened, percent-encoded, full-width): never 'public'
    ("http://0177.0.0.1/", ""), ("http://127.1/", ""), ("http://10.1/", ""), ("http://0xa9.0xfe.0xa9.0xfe/", ""),
    ("http://2130706433/", ""), ("http://10.0.0.%31/", ""), ("http://１０.０.０.１/", ""),
    ("http://169.254.169.254./", ""),
    # trailing dot: same host for the browser
    ("http://claude.ai./", ""), ("http://printer.local./", ""), ("http://metadata.google.internal./", ""),
    ("http://localhost./", "local"), ("http://host.home.arpa/", ""),
    # IPv6 forms that embed a private IPv4
    ("http://[::ffff:192.168.1.1]/", ""), ("http://[64:ff9b::c0a8:101]/", ""), ("http://[fd00::1]/", ""),
    ("http://[::ffff:8.8.8.8]/", "public"), ("http://[::1]/", "local"),
    # userinfo never changes the host
    ("http://public@127.0.0.1/", "local"), ("http://127.0.0.1@example.com/", "public"),
    ("http://user@192.168.0.1/", "")])
def test_page_scope_bypasses(url, scope):
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


@pytest.mark.parametrize("answer", [["10.0.0.1"], ["169.254.169.254"], ["127.0.0.1"], ["100.64.0.1"], ["93.184.216.34", "192.168.1.5"],
                                    ["::1"], ["::ffff:10.0.0.1"], []])
def test_public_name_resolving_to_a_private_address_is_refused(tmp_path, answer):
    """#527: nip.io style names and rebinding answers."""
    s, d, _ = mk(tmp_path, resolver=lambda host: answer)
    with pytest.raises(bp.PilotError) as e:
        s.open("https://10.0.0.1.nip.io/")
    assert e.value.code == "BROWSER_PILOT_URL" and d.opened == []
    assert s.request_allowed("https://img.example.com/x.png") is False


def test_unresolvable_host_fails_closed_and_local_needs_no_dns(tmp_path):
    def boom(host):
        raise OSError("no dns")
    s, d, _ = mk(tmp_path, resolver=boom)
    with pytest.raises(bp.PilotError):
        s.open("https://example.com/")
    assert s.open("http://localhost:8000/")["scope"] == "local" and d.opened == ["http://localhost:8000/"]


def test_redirect_to_a_private_address_stops_the_session(tmp_path):
    s, d, _ = mk(tmp_path, resolver=lambda host: ["10.0.0.9"] if host == "evil.example.com" else ["93.184.216.34"])
    d.final = "https://evil.example.com/inside"
    with pytest.raises(bp.PilotError) as e:
        s.open("https://example.com/")
    assert e.value.code == "BROWSER_PILOT_REDIRECT" and d.closed and s.state == bp.STOPPED
    assert s.request_allowed("https://example.com/a") is True


def test_audit_kind_is_never_a_caller_string(tmp_path):
    """#528 item 1: a URL or secret passed as the kind lands as 'other' in the audit and the receipts."""
    s, d, _ = mk(tmp_path)
    with pytest.raises(bp.PilotError):
        s.act("https://evil.example.com/?k=" + SECRET)
    blob = (tmp_path / bp.AUDIT_FILE).read_text(encoding="utf-8")
    assert SECRET not in blob and "evil.example.com" not in blob and '"kind": "other"' in blob


def test_read_cuts_before_redacting_and_default_clock_is_monotonic(tmp_path, monkeypatch):
    """#528 items 2 and 3."""
    seen = []
    real = bp.logs.redact
    monkeypatch.setattr(bp.logs, "redact", lambda t: seen.append(len(t)) or real(t))
    s, d, _ = mk(tmp_path)
    d.text = lambda: "x" * 10 ** 6
    assert len(s.read()["untrusted_text"]) == bp.MAX_TEXT and max(seen) == 2 * bp.MAX_TEXT
    import inspect
    assert inspect.signature(bp.PilotSession).parameters["clock"].default is bp.time.monotonic


def test_final_url_is_checked_when_current_url_is_a_property_or_unreadable(tmp_path):
    """#566 item 2: a property (string) is checked; a missing or non-string address stops the session (fail closed)."""
    s, d, _ = mk(tmp_path, resolver=lambda host: ["10.0.0.9"] if host == "evil.example.com" else ["93.184.216.34"])
    d.final = "https://evil.example.com/"
    with pytest.raises(bp.PilotError) as e:
        s.open("https://example.com/")
    assert e.value.code == "BROWSER_PILOT_REDIRECT"
    s, d, _ = mk(tmp_path / "b")
    d.final = ""
    with pytest.raises(bp.PilotError) as e:
        s.open("https://example.com/")
    assert e.value.code == "BROWSER_PILOT_REDIRECT" and d.closed


def test_names_are_resolved_once_and_pinned_for_the_driver(tmp_path):
    """#566 item 1: a rebinding server (public first, private next) cannot change what was vetted."""
    answers = iter([["93.184.216.34"], ["10.0.0.1"], ["10.0.0.1"]])
    calls = []
    s, d, _ = mk(tmp_path, resolver=lambda host: calls.append(host) or next(answers))
    s.open("https://example.com/")
    assert s.request_allowed("https://example.com/img.png") is True and calls == ["example.com"]   # one resolution
    assert d.pinned[-1] == {"example.com": ["93.184.216.34"]}
    assert s.request_allowed("https://rebind.example.net/") is False                                 # private answer refused
    s.request_allowed("https://example.com/")
    assert all("rebind.example.net" not in m for m in d.pinned)                                       # never handed to the driver


def test_a_driver_without_the_contract_is_refused(tmp_path):
    class Old:
        def open(self, url): ...
        def text(self): return ""
        def screenshot(self): return b""
        def close(self): ...
    with pytest.raises(TypeError) as e:
        bp.PilotSession(tmp_path, Old(), enabled=lambda: True)
    assert "pin_hosts" in str(e.value) and "current_url" in str(e.value)


def test_system_resolver_has_a_timeout_and_fails_closed(monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(bp.socket, "getaddrinfo", lambda *a, **k: gate.wait(5))
    monkeypatch.setattr(bp, "RESOLVE_TIMEOUT_S", 0.05)
    assert bp.vet_url("https://slow.example.com/") == ""
    gate.set()


def test_567_v4_compatible_ipv6_idna_disagreement_and_cut_tail(tmp_path):
    assert bp.page_scope("http://[::10.0.0.1]/") == "" and bp.page_scope("http://[::1]/") == "local"
    assert bp.vet_url("https://fa\u00dfe.de/", lambda h: ["93.184.216.34"]) == ""          # IDNA 2003 vs UTS 46 differ: refused
    s, d, _ = mk(tmp_path)
    d.text = lambda: "a" * (2 * bp.MAX_TEXT + 500)
    assert len(s.read()["untrusted_text"]) == bp.MAX_TEXT
    d.text = lambda: "ghp_" + "x" * (2 * bp.MAX_TEXT - 4) + "ghp_SECRETSECRETSECRETSECRETSECRET"
    assert "ghp_SECRET" not in s.read()["untrusted_text"]


@pytest.mark.parametrize("url", ["http://[::ffff:0:a00:1]/", "http://[fec0::1]/", "http://[::93.184.216.34]/", "http://[::ffff:10.0.0.1]/"])
def test_575_translated_site_local_and_compat_ipv6_are_refused(url):
    assert bp.page_scope(url) == ""
    host = url[8:-2]
    assert bp.vet_url("https://names.example.com/", lambda h: [host]) == ""
    assert bp.page_scope("http://[::ffff:93.184.216.34]/") == "public" and bp.page_scope("http://[::1]/") == "local"


@pytest.mark.parametrize("url,expect", [("https://example.com/", "public"), ("http://example.com:80/", "public"), ("https://example.com:443/x", "public"),
                                        ("https://example.com:22/", ""), ("http://example.com:8080/", ""), ("https://example.com:99999/", ""),
                                        ("http://localhost:8000/", "local"), ("http://127.0.0.1:3000/", "local")])
def test_574_public_hosts_only_on_web_ports_loopback_any_port(url, expect):
    assert bp.page_scope(url) == expect


def test_574_a_pin_hosts_exception_stops_the_session_and_refuses(tmp_path):
    s, d, _ = mk(tmp_path)

    def boom(m):
        raise RuntimeError("no proxy")
    d.pin_hosts = boom
    with pytest.raises(bp.PilotError) as e:
        s.open("https://example.com/")
    assert e.value.code == "BROWSER_PILOT_PIN" and s.state == bp.STOPPED and d.opened == [] and d.closed
    s2, d2, _ = mk(tmp_path / "b")
    d2.pin_hosts = boom
    assert s2.request_allowed("https://example.com/x") is False and s2.state == bp.STOPPED


def test_574_a_failing_resolver_never_allows_a_request_and_the_dns_pool_is_shared_and_capped(tmp_path, monkeypatch):
    s, d, _ = mk(tmp_path, resolver=lambda h: (_ for _ in ()).throw(RuntimeError("x")))
    assert s.request_allowed("https://example.com/") is False
    assert bp._resolver_pool() is bp._resolver_pool() and bp._resolver_pool()._max_workers == bp.RESOLVER_THREADS


@pytest.mark.parametrize("url", ["https://10.0.0.1" + chr(92) + "@example.com/", "https://example.com:22" + chr(92) + "@example.org/",
                                 "https://127.0.0.1:22" + chr(92) + "@example.com/", "https://example.com/a" + chr(92) + "b"])
def test_574_a_backslash_url_is_refused_browsers_read_it_as_a_slash(url):
    assert bp.page_scope(url) == "" and bp.vet_url(url, lambda h: ["93.184.216.34"]) == ""
