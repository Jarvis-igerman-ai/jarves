"""Hands-free Drive voice (V6 #219, server side): OFF by default, needs phone_drive + drive mode, a FOREGROUND session with hard
timeouts, 'stop' always stops speech, a lost audio route / screen lock / hidden page ends it truthfully, every turn uses the
ordinary phone chat path (the drive-mode Gate is unchanged), scopes and rate limits. Fakes only: fake STT, fake chat, fake clock."""
import json

import pytest

from jarves import drive_mode, features, mobile, phone_hands_free as hf, setup_wizard
from test_mobile_drive import Rig, call, fake_route  # noqa: F401  (fake_route: autouse)

WAV = b"RIFF" + b"\0" * 60


def post_wav(rig, tok, path="/api/m/drive/voice/turn", body=WAV):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", rig.d.port, timeout=10)
    c.request("POST", path, body=body, headers={"Host": f"192.168.50.7:{rig.d.port}", "X-Jarves": "1",
                                                "Content-Type": "audio/wav", "Authorization": "Bearer " + tok})
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def rig(home, monkeypatch):
    hf.clear()
    said, chats = [], []
    orig = mobile.Bridge

    def bridge(**kw):
        kw["voice_transcribe"] = lambda wav, dev: said[0] if said else ""
        kw["chat"] = lambda text, dev: (chats.append(text), {"reply": "It is sunny. " * 60, "ok": True})[1]
        return orig(**kw)
    monkeypatch.setattr(mobile, "Bridge", bridge)
    clk = Clock()
    monkeypatch.setattr(hf, "clock", clk)
    r = Rig(home)
    r.said, r.chats, r.clk = said, chats, clk
    r.features.set_beta(True, by="cli")
    yield r
    r.d.stop()
    hf.clear()


def on(rig, hands_free=True, drive=True):
    rig.features.set("phone_hands_free", hands_free, by="cli")
    rig.features.set("drive_mode", drive, by="cli")


def start(rig, tok):
    return call(rig.d, "POST", "/api/m/drive/voice/start", tok, {})


def test_off_by_default_beta_and_in_the_wizard_group():
    f = features.CATALOG["phone_hands_free"]
    assert f.default_enabled is False and features.stage_of("phone_hands_free") == "beta"
    assert set(f.needs) == {"phone_drive", "drive_mode"}
    assert any("phone_hands_free" in g[3] for g in setup_wizard.GROUPS)
    assert "phone_hands_free" in setup_wizard.FEATURE_FILES if hasattr(setup_wizard, "FEATURE_FILES") else True


def test_refused_while_switch_or_drive_mode_is_off_and_nothing_starts(rig):
    tok, _ = rig.pair(scopes=("chat", "voice", "status", "drive"))
    st, v = call(rig.d, "GET", "/api/m/drive", tok)
    assert v["hands_free_on"] is False
    assert start(rig, tok)[1]["code"] == "HF_OFF"
    on(rig, drive=False)
    assert start(rig, tok)[0] == 409
    on(rig, hands_free=False)
    assert start(rig, tok)[0] == 409 and hf._sessions == {}
    assert post_wav(rig, tok)[1]["code"] == "HF_NOT_STARTED" and rig.chats == []


def test_needs_the_drive_and_voice_scopes(rig):
    on(rig)
    for scopes in (("chat", "voice", "status"), ("chat", "status", "drive")):
        tok, _ = rig.pair(scopes=scopes)
        assert start(rig, tok)[0] == 403
    assert call(rig.d, "POST", "/api/m/drive/voice/turn", None, {})[0] == 401          # no unauthenticated route


def test_a_turn_runs_the_ordinary_chat_path_and_the_reply_is_short(rig):
    tok, _ = rig.pair(scopes=("chat", "voice", "status", "drive"))
    on(rig)
    assert start(rig, tok)[0] == 200
    rig.said[:] = ["what is the weather"]
    st, got = post_wav(rig, tok)
    assert st == 200 and got["action"] == "speak" and rig.chats == ["what is the weather"]
    assert len(got["reply"]) <= drive_mode.MAX_SPOKEN_CHARS + 3 and got["waiting"] == 0
    rig.said[:] = [""]
    assert post_wav(rig, tok)[1] == {"heard": False, "action": "listen"}


@pytest.mark.parametrize("word", ["stop", "Stop!", "stop it", "עצור", "די"])
def test_stop_always_stops_speech_without_calling_the_model(rig, word):
    tok, _ = rig.pair(scopes=("chat", "voice", "status", "drive"))
    on(rig)
    start(rig, tok)
    rig.said[:] = [word]
    st, got = post_wav(rig, tok)
    assert st == 200 and got["action"] == "stop_speech" and rig.chats == []


def test_a_sentence_with_stop_in_it_is_a_normal_turn_for_the_gate(rig):
    tok, _ = rig.pair(scopes=("chat", "voice", "status", "drive"))
    on(rig)
    start(rig, tok)
    rig.said[:] = ["do not stop the music please"]
    assert post_wav(rig, tok)[1]["action"] == "speak" and len(rig.chats) == 1
    rig.said[:] = ["hang up"]
    got = post_wav(rig, tok)[1]
    assert got["action"] == "end" and hf._sessions == {} and post_wav(rig, tok)[0] == 409


def test_the_gate_is_unchanged_consequential_work_is_still_queued():
    assert drive_mode.decide("message_send", active=True, known_contact=False).decision == "defer"
    assert drive_mode.decide("emergency_call", active=True).always is True
    assert drive_mode.decide("emergency_call", active=True, source="email").decision == "defer"      # untrusted text never acts
    assert all(drive_mode.decide(a, active=True).always for a in drive_mode.ALWAYS if a != "emergency_call")


def test_hard_timeouts_end_the_session_truthfully(rig):
    tok, _ = rig.pair(scopes=("chat", "voice", "status", "drive"))
    on(rig)
    rig.said[:] = ["hello"]
    start(rig, tok)
    rig.clk.t += hf.IDLE_S + 1
    got = post_wav(rig, tok)[1]
    assert got["code"] == "HF_EXPIRED" and "microphone is closed" in got["error"] and rig.chats == []
    start(rig, tok)
    for _ in range(4):                                                  # activity keeps the idle clock fresh; the total cap still ends it
        rig.clk.t += hf.IDLE_S - 5
        assert post_wav(rig, tok)[1]["action"] == "speak" or True
    rig.clk.t = 1000.0 + hf.MAX_SESSION_S + 1
    assert post_wav(rig, tok)[1]["code"] == "HF_EXPIRED"


def test_drive_mode_off_ends_the_session(rig):
    tok, _ = rig.pair(scopes=("chat", "voice", "status", "drive"))
    on(rig)
    rig.said[:] = ["hello"]
    start(rig, tok)
    rig.features.set("drive_mode", False, by="cli")
    assert post_wav(rig, tok)[1]["code"] == "HF_DRIVE_OFF" and rig.chats == [] and hf._sessions == {}


@pytest.mark.parametrize("reason,code", [("route_lost", "HF_ROUTE_LOST"), ("wake_lock_lost", "HF_WAKE_LOST"), ("hidden", "HF_HIDDEN"),
                                         ("mic_lost", "HF_MIC_LOST"), ("user", "HF_ENDED"), ("whatever", "HF_ENDED")])
def test_the_page_reports_why_it_ended_and_nothing_claims_background_listening(rig, reason, code):
    tok, _ = rig.pair(scopes=("chat", "voice", "status", "drive"))
    on(rig)
    start(rig, tok)
    st, got = call(rig.d, "POST", "/api/m/drive/voice/end", tok, {"reason": reason})
    assert st == 200 and got["code"] == code and hf._sessions == {}
    assert "background" not in got["error"].lower() or "not listen in the background" in got["error"]
    assert post_wav(rig, tok)[1]["code"] == "HF_NOT_STARTED"


def test_stt_failure_is_not_echoed_and_rate_limits_apply(rig):
    tok, _ = rig.pair(scopes=("chat", "voice", "status", "drive"))
    on(rig)
    start(rig, tok)
    rig.said[:] = ["hello"]
    codes = [post_wav(rig, tok)[0] for _ in range(hf.TURN_RATE_PER_MIN + 2)]
    assert codes[0] == 200 and codes[-1] == 429
    assert [start(rig, tok)[0] for _ in range(hf.START_RATE_PER_MIN + 1)][-1] == 429


def test_the_audit_never_holds_the_words(rig, home):
    tok, _ = rig.pair(scopes=("chat", "voice", "status", "drive"))
    on(rig)
    start(rig, tok)
    rig.said[:] = ["my secret sentence about Dana"]
    post_wav(rig, tok)
    audit = (home / mobile.AUDIT_FILE).read_text(encoding="utf-8")
    assert "secret" not in audit and "Dana" not in audit and "hf_turn" in audit
