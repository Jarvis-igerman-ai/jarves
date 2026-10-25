"""Phone microphone / cloud-audio indicator and notice stop / snooze (V5 gap G5, issue #185): the indicator data is true
state (read from the computer, "unknown" when unreadable, never a calm guess), stop / snooze reduce activity and work with
the default status scope, switching notices on stays on the computer, static files."""
import http.client
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarves import dashboard, mobile, mobile_audio
from jarves.config import Config
from jarves.mobile import Bridge

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
    out = json.loads(r.read() or b"{}")
    c.close()
    return r.status, out


def make(home, audio):
    from conftest import free_port
    kw = {} if audio is None else {"audio": audio}
    b = Bridge(chat=lambda t, d: {"reply": "x", "ok": True}, voice=lambda w, d: {}, status=lambda: {"core": "running", "components": {}}, **kw)
    return dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=b).start()


def pair(d, scopes=("chat", "status")):
    st, code = call(d, "POST", "/api/mobile/code", body={}, owner=True)
    st, got = call(d, "POST", "/api/m/pair", body={"code": code["code"], "name": "Galaxy"})
    assert st == 200
    if scopes is not None:
        assert call(d, "POST", f"/api/mobile/devices/{got['device']}/scopes", body={"scopes": list(scopes)}, owner=True)[0] == 200
    return got["token"], got["device"]


@pytest.fixture
def srv(home):
    d = make(home, lambda: {"known": True, "mic": "listening", "cloud": "sending", "cloud_on": True})
    yield d
    d.stop()


def test_audio_state_needs_a_token_and_the_status_scope(srv):
    assert call(srv, "GET", "/api/m/audio")[0] == 401
    tok, _ = pair(srv, scopes=("chat",))
    assert call(srv, "GET", "/api/m/audio", tok)[0] == 403
    tok, _ = pair(srv)
    assert call(srv, "GET", "/api/m/audio", tok)[0] == 200


def test_the_computers_real_state_and_the_fixed_phone_path_are_reported(srv):
    tok, _ = pair(srv)
    st, v = call(srv, "GET", "/api/m/audio", tok)
    assert v["computer"] == {"known": True, "mic": "listening", "cloud": "sending", "cloud_on": True}
    assert v["phone"]["cloud"] is False and v["phone"]["stored"] is False and v["phone"]["route"] == "computer_local"
    assert v["phone"]["voice"] is False                                                    # plain http: no phone microphone


def test_an_unreadable_state_is_unknown_never_calm(home):
    def boom():
        raise RuntimeError("no state")
    for audio in (boom, None):
        d = make(home, audio)
        try:
            tok, _ = pair(d)
            c = call(d, "GET", "/api/m/audio", tok)[1]["computer"]
            assert c == {"known": False, "mic": "unknown", "cloud": "unknown", "cloud_on": None}
        finally:
            d.stop()


def test_the_core_adapter_uses_the_privacy_state_source():
    live = SimpleNamespace()
    core = SimpleNamespace(health=SimpleNamespace(components={}), dev=SimpleNamespace(mic_muted=False, face_state=""), live=live,
                           listening=False, cfg=None, camera=None)
    c = mobile.core_bridge(core, None).audio()
    assert c == {"known": True, "mic": "listening", "cloud": "sending", "cloud_on": True}      # a live session streams to the cloud
    core.live = None
    assert mobile.core_bridge(core, None).audio() == {"known": True, "mic": "idle", "cloud": "off", "cloud_on": False}
    core.dev.mic_muted = True
    assert mobile.core_bridge(core, None).audio()["mic"] == "muted"
    assert mobile_audio.from_privacy_state({}) == {"known": True, "mic": "unknown", "cloud": "unknown", "cloud_on": False}


def test_snooze_and_stop_work_with_the_default_scopes_and_stay_idempotent(srv):
    srv.mobile.cont.configure_signals(on=True)
    tok, _ = pair(srv)                                                                     # chat + status only (no tasks)
    st, out = call(srv, "POST", "/api/m/signals", tok, {"op": "snooze", "minutes": 60, "id": "sig-aaaaaaaa"})
    assert st == 200 and out["snoozed_until"] > 0
    again = call(srv, "POST", "/api/m/signals", tok, {"op": "snooze", "minutes": 60, "id": "sig-aaaaaaaa"})
    assert again[1]["replay"] is True
    assert call(srv, "POST", "/api/m/signals", tok, {"op": "on", "id": "sig-bbbbbbbb"})[0] == 400      # switching on: the computer only
    assert call(srv, "POST", "/api/m/signals", tok, {"op": "stop", "id": "sig-cccccccc"})[0] == 200
    assert srv.mobile.cont.signals_state()["stopped"] is True
    _, s = call(srv, "GET", "/api/m/sync?since=0", tok)
    assert s["signals"]["stopped"] is True                                                 # the phone can show the state
    tok2, _ = pair(srv, scopes=("chat",))                                                  # neither status nor tasks: refused
    assert call(srv, "POST", "/api/m/signals", tok2, {"op": "stop", "id": "sig-dddddddd"})[0] == 403
    assert call(srv, "POST", "/api/m/signals", tok, {"op": "stop"})[0] == 400              # command id required


def test_a_stopped_signal_note_is_cleared_for_the_phone(srv):
    c = srv.mobile.cont
    c.configure_signals(on=True)
    tok, _ = pair(srv)
    assert c.emit("goal", "7", "Goal 7 looks stalled") == "queued"
    _, s = call(srv, "GET", "/api/m/sync?since=0", tok)
    assert [n["kind"] for n in s["notices"]] == ["signal"]
    assert call(srv, "POST", "/api/m/signals", tok, {"op": "stop", "id": "sig-eeeeeeee"})[0] == 200
    assert c.emit("goal", "8", "Goal 8 looks stalled") == "stopped"                        # nothing more is created
    assert call(srv, "GET", f"/api/m/sync?since={s['head']}", tok)[1]["notices"] == []


# ---------------------------------------------------------------- static files
def test_indicator_js_is_served_csp_safe_and_translated():
    js = (MOBILE_DIR / "indicator.js").read_text(encoding="utf-8")
    assert "indicator.js" in mobile.STATIC_FILES and "innerHTML" not in js and "eval(" not in js and "Authorization" not in js

    def body(lang):
        m = re.search(rf"\n    {lang}: \{{(.*?)(?=\n    (?:he|en): \{{|\n  \}};)", js, re.S)
        return set(re.findall(r"\b([a-z_]+):\s*\"", m.group(1)))
    en, he = body("en"), body("he")
    assert en == he and len(en) > 25
    assert set(re.findall(r'\bt\("([a-z_]+)"\)', js)) <= en
    from jarves.runtime import privacy_state as ps
    assert {"c_" + x for x in ps.CLOUD} <= en and set(ps.MIC) <= en                       # every state the computer can report has words
    assert re.search(r"[֐-׿]", js)
    html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
    assert "/m/indicator.js" in html and 'id="audioBar"' in html and 'id="noticeBox"' in html
    app = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
    assert re.search(r"const newId = ", app)                                              # the helper the goal / snooze buttons call


def test_the_audio_reply_carries_a_server_timestamp_and_the_page_has_timeout_and_staleness(srv):
    import time
    tok, _ = pair(srv)
    t0 = time.time()
    v = call(srv, "GET", "/api/m/audio", tok)[1]
    assert t0 - 1 <= v["ts"] <= time.time() + 1
    js = (MOBILE_DIR / "indicator.js").read_text(encoding="utf-8")
    assert re.search(r"TIMEOUT_MS = 5000", js) and re.search(r"STALE_MS = 8000", js) and "phone.live" in js
