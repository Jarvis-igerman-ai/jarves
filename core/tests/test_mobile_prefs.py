"""Phone page for Learned preferences (V5 gap G3, issue #183): scope, list / confirm / edit / forget, the same rules
as the dashboard, idempotent commands, privacy mode, no text in the audit log or the command ledger, static files."""
import http.client
import json
import re
from pathlib import Path

import pytest

from jarves import dashboard, logs, mobile
from jarves.config import Config
from jarves.preferences import Preferences
from jarves.userdata import open_db

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


@pytest.fixture
def srv(home):
    from conftest import free_port
    from jarves.mobile import Bridge
    b = Bridge(chat=lambda t, d: {"reply": "x", "ok": True}, voice=lambda w, d: {}, status=lambda: {"core": "running", "components": {}})
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=b).start()
    yield d
    d.stop()


def pair(d, scopes=("chat", "status", "prefs")):
    st, code = call(d, "POST", "/api/mobile/code", body={}, owner=True)
    st, got = call(d, "POST", "/api/m/pair", body={"code": code["code"], "name": "Galaxy"})
    assert st == 200
    assert call(d, "POST", f"/api/mobile/devices/{got['device']}/scopes", body={"scopes": list(scopes)}, owner=True)[0] == 200
    return got["token"], got["device"]


@pytest.fixture
def prefs(home):
    p = Preferences(open_db(home))
    a = p.add_confirmed("I like short answers in the morning", "communication")["item"]["id"]
    b = p.suggest("Play jazz while cooking", "I love jazz when I cook", "music")["item"]["id"]
    return p, a, b


_n = [0]


def cid():
    _n[0] += 1
    return f"pref-test-{_n[0]:06d}"


def op(d, tok, **body):
    return call(d, "POST", "/api/m/prefs/op", tok, {"id": cid(), **body})


def test_the_page_needs_the_prefs_scope_and_a_token(srv, prefs):
    assert call(srv, "GET", "/api/m/prefs")[0] == 401                                  # no token: no unauthenticated route
    assert call(srv, "POST", "/api/m/prefs/op", None, {"id": cid(), "op": "remove", "pref": 1})[0] == 401
    tok, _ = pair(srv, scopes=("chat", "status", "approvals", "voice"))               # the default scopes: not granted
    assert call(srv, "GET", "/api/m/prefs", tok)[0] == 403
    assert op(srv, tok, op="remove", pref=prefs[1])[0] == 403
    assert prefs[0].get(prefs[1]) is not None                                          # nothing happened
    tok, _ = pair(srv)
    assert call(srv, "GET", "/api/m/prefs", tok)[0] == 200


def test_list_confirm_edit_forget_show_on_the_pc_too(srv, prefs):
    p, a, b = prefs
    tok, _ = pair(srv)
    st, v = call(srv, "GET", "/api/m/prefs", tok)
    assert st == 200 and [x["id"] for x in v["items"]] == [a] and [x["id"] for x in v["proposed"]] == [b]
    assert v["in_prompt"] is True and "music" in v["topics"]
    assert op(srv, tok, op="confirm", pref=b)[1]["state"] == "confirmed"
    assert op(srv, tok, op="edit", pref=a, text="I like very short answers in the morning")[0] == 200
    _, pc = call(srv, "GET", "/api/prefs", owner=True)                                 # the PC dashboard page: same store
    assert sorted(x["text"] for x in pc["items"]) == ["I like very short answers in the morning", "Play jazz while cooking"]
    assert op(srv, tok, op="remove", pref=a)[1]["ok"] is True
    assert op(srv, tok, op="decline", pref=b)[1]["ok"] is True
    _, v = call(srv, "GET", "/api/m/prefs", tok)
    assert v["items"] == [] and [x["id"] for x in v["declined"]] == [b]
    assert p.get(a) is None


def test_the_same_rules_as_the_desktop(srv, prefs):
    p, a, b = prefs
    tok, _ = pair(srv)
    st, out = op(srv, tok, op="edit", pref=a, text="my password is hunter2")           # sensitive text: refused, not stored
    assert st == 400 and "sensitive" in out["error"] and p.get(a)["text"] == "I like short answers in the morning"
    assert op(srv, tok, op="edit", pref=a, text="  ")[0] == 400
    assert op(srv, tok, op="edit", pref=a, text="Play jazz while cooking")[0] == 400   # a duplicate
    assert op(srv, tok, op="add", pref=a, text="new one")[0] == 400                    # a phone cannot add or invent one
    assert op(srv, tok, op="clear", pref=a)[0] == 400                                  # nor clear everything
    assert op(srv, tok, op="remove", pref=9999)[0] == 404
    assert op(srv, tok, op="remove", pref="x")[0] == 400
    assert call(srv, "POST", "/api/m/prefs/op", tok, {"op": "remove", "pref": a})[0] == 400          # command id required
    assert p.get(a) is not None


def test_a_resent_command_runs_once(srv, prefs):
    p, a, b = prefs
    tok, _ = pair(srv)
    body = {"id": "pref-resend-0001", "op": "remove", "pref": a}
    assert call(srv, "POST", "/api/m/prefs/op", tok, body)[1]["ok"] is True
    p.add_confirmed("A new one", "other")
    again = call(srv, "POST", "/api/m/prefs/op", tok, body)                            # the phone retried after a lost answer
    assert again[0] == 200 and again[1]["replay"] is True
    assert p.get(a) is None and len(p.items("confirmed")) == 1                         # not removed twice, nothing else touched


def test_privacy_mode_keeps_no_text_in_the_audit_the_ledger_or_the_logs(srv, prefs, home, monkeypatch):
    p, a, b = prefs
    tok, _ = pair(srv)
    monkeypatch.setattr(logs, "privacy", lambda: True)
    _, v = call(srv, "GET", "/api/m/prefs", tok)
    assert v["privacy"] is True
    assert [x["text"] for x in v["items"]] == ["I like short answers in the morning"]     # the owner's phone sees it
    op(srv, tok, op="edit", pref=a, text="Brief answers please, every morning")
    op(srv, tok, op="confirm", pref=b)
    audit = (home / mobile.AUDIT_FILE).read_text(encoding="utf-8")
    assert "Brief answers" not in audit and "jazz" not in audit and '"pref"' in audit
    ledger = srv.mobile.cont._rows("SELECT body FROM ledger")
    assert ledger and not any("Brief" in r["body"] or "jazz" in r["body"] for r in ledger)


def test_a_dashboard_route_is_still_closed_to_a_phone_token(srv, prefs):
    tok, _ = pair(srv)
    assert call(srv, "GET", "/api/prefs", tok)[0] == 403
    assert call(srv, "POST", "/api/prefs/op", tok, {"op": "remove", "id": prefs[1]})[0] == 403


def test_the_owner_can_grant_and_revoke_the_scope_on_the_pc(srv, home, capsys):
    from jarves import cli
    _, dev = pair(srv, scopes=("chat",))
    assert cli.main(["phone", "scopes", dev, "--grant", "prefs"]) == 0
    assert "prefs" in capsys.readouterr().out and "prefs" in mobile.MobileAccess(home).devices()[0]["scopes"]
    assert cli.main(["phone", "scopes", dev, "--revoke", "prefs"]) == 0
    assert "prefs" not in mobile.MobileAccess(home).devices()[0]["scopes"]
    assert cli.main(["phone", "scopes", "000000000000"]) == 1


# ---------------------------------------------------------------- static files
def test_prefs_js_is_served_csp_safe_and_translated():
    js = (MOBILE_DIR / "prefs.js").read_text(encoding="utf-8")
    assert "prefs.js" in mobile.STATIC_FILES and "innerHTML" not in js and "eval(" not in js and "Authorization" not in js

    def body(lang):
        m = re.search(rf"\n    {lang}: \{{(.*?)(?=\n    (?:he|en): \{{|\n  \}};)", js, re.S)
        return set(re.findall(r"\b([a-z_]+):\s*\"", m.group(1)))
    en, he = body("en"), body("he")
    assert en == he and len(en) > 20
    assert set(re.findall(r'\bt\("([a-z_]+)"\)', js)) <= en
    assert re.search(r"[֐-׿]", js)
    html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
    assert '/m/prefs.js' in html and 'id="prefsRoot"' in html and 'id="tabbtn-prefs"' in html
