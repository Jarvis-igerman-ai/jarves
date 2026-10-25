"""Appearance (SW-V2 M2): face / 2D avatar / 3D avatar / hidden - one persisted choice, switched by voice, the
offline command, the dashboard or a file edit; the Core only sends `appearance.set` and nothing else changes."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from jarves import appearance as ap
from jarves import features
from jarves.brain import match_intent
from jarves.skills import SkillRegistry


@pytest.mark.parametrize("text,mode", [
    ("avatar3d", "avatar3d"), ("אווטאר תלת מימד", "avatar3d"), ("לאווטאר תלת-מימדי", "avatar3d"),
    ("3D avatar", "avatar3d"), ("אווטאר דו מימדי", "avatar2d"), ("avatar", "avatar2d"), ("אוואטר", "avatar2d"),
    ("פנים", "face"), ("הפנים הרגילות", "face"), ("בלי פנים", "hidden"), ("מצב מוסתר", "hidden"), ("hidden", "hidden"),
])
def test_normalize(text, mode):
    assert ap.normalize(text) == mode


@pytest.mark.parametrize("bad", ["", "banana", None, 7, ["face"], "x" * 500])
def test_normalize_rejects(bad):
    assert ap.normalize(bad) is None


@pytest.mark.parametrize("said,mode", [
    ("תחליף לאווטאר תלת מימד", "avatar3d"), ("ג'ארביס, תחליף לאווטאר תלת מימד", "avatar3d"),
    ("תעבור לאווטאר דו מימדי", "avatar2d"), ("תחזור לפנים", "face"), ("תשנה את המראה לאווטאר", "avatar2d"),
    ("switch to 3d avatar", "avatar3d"), ("תעבור למצב מוסתר", "hidden"),
])
def test_offline_command(said, mode):
    it = match_intent(said)
    assert it is not None and it.tool == "set_appearance"
    assert ap.normalize(it.args["mode"]) == mode


@pytest.mark.parametrize("said", ["תנגן שיר של עומר אדם", "מה השעה", "תחליף שיר", "תעבור לשיר הבא"])
def test_offline_command_does_not_steal_other_phrases(said):
    it = match_intent(said)
    assert it is None or it.tool != "set_appearance"


def test_persistence_and_bad_file(tmp_path):
    a = ap.Appearance(tmp_path)
    assert a.mode == "face"
    a.set("אווטאר תלת מימד", by="voice")
    assert ap.Appearance(tmp_path).mode == "avatar3d"
    raw = json.loads((tmp_path / "appearance.json").read_text(encoding="utf-8"))
    assert raw["mode"] == "avatar3d" and raw["changed"]["by"] == "voice"
    with pytest.raises(ValueError):
        a.set("banana")
    (tmp_path / "appearance.json").write_text("{not json", encoding="utf-8")
    assert ap.Appearance(tmp_path).mode == "face"            # broken file -> default, never a crash


def test_status_has_truthful_platform_targets(tmp_path):
    st = ap.Appearance(tmp_path).status()
    assert [m["id"] for m in st["modes"]] == list(ap.MODES)
    for m in st["modes"]:
        pi = m["targets"]["linux_arm64_pi"]
        assert pi["status"] == "planned" and pi["blocker"] and pi["roadmap_issue"].startswith(
            "https://github.com/Jarvis-igerman-ai/jarves/issues/")
        assert m["targets"]["windows"]["status"] in ("verified_on_device", "implemented_unverified")
    f = features.CATALOG["appearance"]
    assert f.targets["linux_arm64_pi"]["status"] == "planned" and "set_appearance" in f.tools


async def test_voice_tool_switches_and_pushes(tmp_path):
    sent = []

    async def send(t, p):
        sent.append((t, p))
        return True
    a = ap.Appearance(tmp_path, send=send)
    reg = SkillRegistry()
    reg.allowed_actions |= {"set_appearance"}
    reg.register(ap.make_skill(a))
    res = await reg.call("set_appearance", {"mode": "אווטאר תלת מימד"}, origin="model")
    assert res.ok and res.data["mode"] == "avatar3d" and res.data["shown"]
    assert sent == [("appearance.set", {"mode": "avatar3d"})]
    res = await reg.call("set_appearance", {"mode": "avatar3d"}, origin="model")
    assert res.ok and len(sent) == 1                          # already there: nothing sent again
    res = await reg.call("set_appearance", {"mode": "banana"}, origin="model")
    assert not res.ok and res.code == "APPEARANCE_UNKNOWN" and a.mode == "avatar3d"
    res = await reg.call("set_appearance", {"mode": 3}, origin="model")
    assert not res.ok and res.code == "TOOL_BAD_ARGS"
    res = await reg.call("appearance_get", {}, origin="model")
    assert res.ok and res.data["mode"] == "avatar3d"


async def test_no_face_connected_still_saves(tmp_path):
    async def send(t, p):
        return False                                          # GodotLink.send_face with no face
    a = ap.Appearance(tmp_path, send=send)
    reg = SkillRegistry()
    reg.allowed_actions |= {"set_appearance"}
    reg.register(ap.make_skill(a))
    res = await reg.call("set_appearance", {"mode": "avatar2d"}, origin="model")
    assert res.ok and not res.data["shown"] and ap.Appearance(tmp_path).mode == "avatar2d"


async def test_attach_pushes_on_connect_and_follows_dashboard(tmp_path):
    sent, inner_called = [], []

    async def send_face(t, p):
        sent.append((t, p))
        return True

    async def inner():
        inner_called.append(1)
    core = SimpleNamespace(home=tmp_path, registry=SkillRegistry(), _bg=[],
                           link=SimpleNamespace(send_face=send_face, on_face_connected=inner))
    a = ap.attach(core)
    try:
        assert "set_appearance" in core.registry.allowed_actions and core.registry.get("set_appearance")
        await core.link.on_face_connected()                   # the face (re)connects
        assert inner_called == [1] and sent[-1] == ("appearance.set", {"mode": "face"})
        # the dashboard (another process) switches: the Core pushes within ~1 s
        code, body = ap.api_set(SimpleNamespace(home=tmp_path), None, {"mode": "hidden"})
        assert code == 200 and body["mode"] == "hidden"
        for _ in range(450):
            await asyncio.sleep(0.1)
            if sent[-1] == ("appearance.set", {"mode": "hidden"}):
                break
        assert sent[-1] == ("appearance.set", {"mode": "hidden"})
        assert a.mode == "hidden"
        code, body = ap.api_set(SimpleNamespace(home=tmp_path), None, {"mode": ["face"]})
        assert code == 400
        code, body = ap.api_get(SimpleNamespace(home=tmp_path), None, None)
        assert code == 200 and body["mode"] == "hidden"
    finally:
        for t in core._bg:
            t.cancel()
        await asyncio.gather(*core._bg, return_exceptions=True)
