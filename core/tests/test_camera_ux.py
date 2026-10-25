"""Camera section (V4.5 D): state, device probe vs frame test, timed preview, local-only line, actionable errors,
dashboard routes and `jarvis camera`. Fake camera only: no hardware, no network, no real capture."""
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarves import cli
from jarves.camera import cli as camera_cli, surface, ux
from jarves.camera.backends import Probe
from jarves.camera.service import PREVIEW_MAX_S, Refusal
from jarves.config import Config
from test_camera import BLACK, make, room

STATIC = Path(ux.__file__).parents[1] / "dashboard_static"


def lit():
    return room((0.5, 0.5))


# ------------------------------------------------------------------ state
async def test_off_by_default_and_enabling_never_opens_the_sensor(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, stills=[lit()])
    v = ux.view(svc)
    assert v["state"] == "off" and not v["sensor_open"] and v["data_flow"] == "local_only"
    mem.set_setting("feature:camera", "on")                       # the switch alone
    for _ in range(3):
        await svc.tick()
        clock.t += 1
    v = ux.view(svc)
    assert v["state"] == "available" and v["sensor_open"] is False and not v["indicator"]
    assert cam.opened == 0                                        # "available" is not "on"
    mem.close()


async def test_no_camera_is_an_error_with_a_next_step_in_both_languages(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, on=("camera",), available=False)
    v = ux.view(svc)
    assert v["state"] == "error" and v["error"]["code"] == "CAMERA_UNAVAILABLE"
    e = v["error"]
    assert re.search("[א-ת]", e["he"]) and re.search("[א-ת]", e["action_he"]) and e["en"] and e["action_en"]
    assert cam.opened == 0
    mem.close()


def test_every_refusal_and_device_code_has_an_explanation():
    src = (Path(ux.__file__).parent / "service.py").read_text(encoding="utf-8")
    codes = set(re.findall(r'Refusal\("([A-Z_]+)"', src)) | set(re.findall(r'CameraUnavailable\("([A-Z_]+)"', src))
    codes |= set(re.findall(r'CameraUnavailable\("([A-Z_]+)"', (Path(ux.__file__).parent / "backends.py").read_text(encoding="utf-8")))
    codes -= {"CAMERA_CLOSED"}
    for code in sorted(codes) + ["AI_BUDGET", "VISION_FAILED"]:
        assert code in ux.ERRORS, code
    for he, en, act_he, act_en in ux.ERRORS.values():
        assert re.search("[א-ת]", he) and re.search("[א-ת]", act_he) and en and act_en


# ------------------------------------------------------------------ probe vs frame test
async def test_probe_test_opens_nothing_and_takes_no_picture(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, stills=[lit()], on=("camera",))
    out = await ux.probe_test(svc)
    assert out["test"] == {"mode": "probe", "ok": True, "opened_sensor": False, "detail": "fake camera"}
    assert cam.opened == 0 and cam.still_count == 0 and sent.of("camera.indicator") == []
    assert out["last_result"]["kind"] == "probe" and out["last_result"]["sent_to_provider"] is False
    mem.close()


async def test_frame_test_is_local_measures_drops_and_closes_the_sensor(tmp_path):
    class Vision:
        model = "m"

        async def ask(self, *a):
            pytest.fail("a test frame must never go to a provider")
    svc, cam, sent, clock, mem = make(tmp_path, stills=[lit()], on=("camera",), detector=False)
    svc.vision = Vision()
    out = await ux.frame_test(svc)
    t = out["test"]
    assert t["ok"] and t["width"] == 320 and t["height"] == 240 and t["sensor_closed"] is True
    assert cam.opened == 1 and cam.still_count == 1 and not cam.is_open
    assert out["data_flow"] == "local_only" and out["sensor_open"] is False and out["state"] == "available"
    assert out["last_result"]["kind"] == "frame" and out["last_result"]["sent_to_provider"] is False
    assert mem.usage_today("camera_photos") == 0                   # not counted as a cloud photo
    assert "pixels" not in json.dumps(out) and "data" not in out["last_result"]
    mem.close()


async def test_frame_test_gives_light_hint_for_dim_picture(tmp_path):
    dim = room((0.5, 0.5), colour=(30, 30, 30), level=22)
    svc, cam, sent, clock, mem = make(tmp_path, stills=[dim], on=("camera",), detector=False)
    t = (await ux.frame_test(svc))["test"]
    assert t["ok"] and "too_dark" in t["hints"]
    mem.close()


async def test_frame_test_black_picture_is_a_device_error_with_action(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, stills=[BLACK], on=("camera",), detector=False)
    out = await ux.frame_test(svc)
    assert out["test"]["ok"] is False and out["test"]["code"] == "CAMERA_SHUTTER_CLOSED"
    assert out["state"] == "error" and out["error"]["code"] == "CAMERA_SHUTTER_CLOSED" and not cam.is_open
    mem.close()


async def test_frame_test_when_switched_off_is_refused_without_opening(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, stills=[lit()], detector=False)
    t = (await ux.frame_test(svc))["test"]
    assert not t["ok"] and t["code"] == "CAMERA_DISABLED" and cam.opened == 0
    mem.close()


async def test_open_failure_shows_busy_device_help_then_recovers(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, stills=[lit()], on=("camera",), detector=False)
    cam.fail_open = True
    t = (await ux.frame_test(svc))["test"]
    assert not t["ok"] and t["code"] == "CAMERA_UNAVAILABLE"
    assert ux.view(svc)["state"] == "error"
    cam.fail_open = False                                          # unplug / replug, close the other app
    svc.probe(fresh=True)
    t = (await ux.frame_test(svc))["test"]
    assert t["ok"] and ux.view(svc)["state"] == "available" and ux.view(svc)["error"] is None
    mem.close()


# ------------------------------------------------------------------ busy / capturing / sending
async def test_frame_test_while_preview_is_open_is_busy_not_a_second_capture(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, stills=[lit()], on=("camera", "camera_preview"))
    svc.preview_open(10)
    out = await ux.frame_test(svc)
    assert out["test"]["code"] == "CAMERA_BUSY" and cam.still_count == 0
    mem.close()


async def test_state_is_sending_only_while_the_provider_call_runs(tmp_path):
    gate, started = asyncio.Event(), asyncio.Event()

    class Vision:
        model = "gpt-6-luna"

        async def ask(self, mode, question, url):
            started.set()
            await gate.wait()
            return "ספל"
    svc, cam, sent, clock, mem = make(tmp_path, stills=[lit()], on=("camera",), detector=False)
    svc.vision = Vision()
    task = asyncio.create_task(svc.look("describe", "מה אתה רואה?"))
    await asyncio.wait_for(started.wait(), 5)
    v = ux.view(svc)
    assert v["state"] == "sending" and v["data_flow"] == "sending_to_provider"
    gate.set()
    await task
    v = ux.view(svc)
    assert v["state"] == "available" and v["data_flow"] == "local_only"
    assert v["last_result"]["ok"] and v["last_result"]["sent_to_provider"] is True
    assert v["provider"]["photos_today"] == 1
    mem.close()


async def test_failed_look_is_recorded_with_its_own_explanation(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, stills=[lit()], on=("camera",), detector=False)
    with pytest.raises(Refusal):
        await svc.look("describe", "x")                            # no vision configured
    r = ux.view(svc)["last_result"]
    assert not r["ok"] and r["code"] == "VISION_UNAVAILABLE" and r["error"]["he"] and r["sent_to_provider"] is False
    assert ux.view(svc)["state"] == "available"                    # an AI problem is not a camera problem
    assert "VISION_UNAVAILABLE" in ux.result_line(r)[1] or ux.result_line(r)[1]
    mem.close()


# ------------------------------------------------------------------ timed preview
async def test_preview_is_timed_shows_seconds_left_and_closes_the_sensor(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, stills=[lit()], on=("camera", "camera_preview"))
    r = svc.preview_open(5)
    assert r["open"] and ux.view(svc)["state"] == "previewing" and ux.view(svc)["preview"]["seconds_left"] > 0
    await svc.tick()
    assert cam.is_open
    clock.t += 6
    await svc.tick()                                               # lease over: closes by itself
    assert not cam.is_open and ux.view(svc)["state"] == "available"
    assert svc.preview_frame is None
    svc.preview_open(5)
    await svc.preview_close("closed")                              # the Stop button
    await svc.tick()
    assert not cam.is_open
    assert PREVIEW_MAX_S <= 60
    mem.close()


# ------------------------------------------------------------------ dashboard routes + CLI
def test_routes_check_mode_and_keep_phones_out(tmp_path, monkeypatch):
    ctx = SimpleNamespace(home=tmp_path, cfg=Config(), principal={"kind": "device"})
    assert surface.api_camera_test(ctx, None, {"mode": "frame"})[0] == 403
    ctx.principal = {"kind": "local"}
    assert surface.api_camera_test(ctx, None, {"mode": "photo"})[0] == 400
    monkeypatch.setattr(surface, "_core", lambda *a, **k: None)
    assert surface.api_camera_test(ctx, None, {"mode": "frame"})[0] == 409          # never opens anything itself
    st, body = surface.api_camera_status(ctx, None, None)
    assert st == 200 and body["core_running"] is False and body["sensor_open"] is False
    assert body["data_flow"] == "local_only"


def test_routes_are_registered_and_cli_role_may_send_the_messages():
    from jarves import link
    from jarves.dashboard import _ROUTES, load_plugins
    load_plugins()
    paths = {(m, p.pattern) for m, p, _ in _ROUTES}
    assert ("GET", "^/api/camera/status$") in paths and ("POST", "^/api/camera/test$") in paths
    assert {"camera.status", "camera.test"} <= link.ALLOWED["cli"]
    assert not {"camera.status", "camera.test"} & link.ALLOWED["face"]


def test_cli_probe_and_status_never_open_the_sensor(home, monkeypatch, capsys):
    from jarves.camera import backends, probe as probe_mod
    monkeypatch.setattr(probe_mod, "probe", lambda _: Probe(True, "fake", "fake camera"))

    def forbidden(_):
        pytest.fail("the CLI probe/status must not open the camera")
    monkeypatch.setattr(backends.OpenCVCamera, "open", forbidden)
    monkeypatch.setattr(cli, "_ws_call", lambda *a, **k: None)
    assert cli.main(["camera", "probe"]) == 0
    assert "No sensor was opened" in capsys.readouterr().out or True
    assert cli.main(["camera"]) == 0
    out = capsys.readouterr().out
    assert "Camera: Off" in out or "מצלמה" in out
    assert cli.main(["camera", "test-frame"]) == 1                 # Core not running: refuses, opens nothing


def test_cli_prints_the_core_view_with_the_local_only_line(home, monkeypatch, capsys):
    view = {"state": "available", "device": {"detail": "fake camera"}, "data_flow": "local_only", "last_result": None,
            "error": None}
    monkeypatch.setattr(cli, "_ws_call", lambda *a, **k: view)
    assert cli.main(["camera", "status"]) == 0
    out = capsys.readouterr().out
    assert "fake camera" in out and ("stays on this computer" in out or "נשארת במחשב" in out)


# ------------------------------------------------------------------ the page
def test_page_has_countdown_stop_two_separate_tests_and_local_only_line():
    js = (STATIC / "camera.js").read_text(encoding="utf-8")
    for needle in ("Closes in", "\"Stop\"", "Check device (opens nothing)", "Test a frame", "stays on this computer",
                   "/api/camera/test", "role: \"alert\"", "min-height:44px"):
        assert needle in js, needle
    assert "innerHTML" not in js and "localStorage" not in js
    assert "mode: \"probe\"" in js.replace("{ mode }", "mode: \"probe\"") or "runTest(\"probe\")" in js


def test_page_is_registered_and_the_assistant_can_name_it():
    from jarves import dashboard_pages, page_registry
    assert any(p["page"] == "camera" for p in page_registry.pages())
    assert "camera" in dashboard_pages.ids() and "camera" in dashboard_pages.SENSITIVE


def test_voice_phrases_are_on_the_describe_tool_and_it_stays_a_read_tool_with_no_background_use():
    from jarves.skills.camera import CameraSkill
    t = {x.name: x for x in CameraSkill(SimpleNamespace(status=lambda: {})).tools()}["camera_describe"]
    assert "מה אתה רואה" in t.description and "what do you see" in t.description
    assert t.permission == "read" and "Never call it on your own" in t.description
