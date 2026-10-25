"""Dashboard routes of the Camera section (V4.5 D). The Core owns the sensor, so status and tests go to the running
Core over the local link (`camera.status` / `camera.test`); with the Core stopped the section shows a sensorless
probe and says so. Local computer only: a paired phone never sees camera state-changing routes or pictures."""
from __future__ import annotations


def _local_only(ctx):
    who = getattr(ctx, "principal", None)
    if who is not None and who.get("kind") != "local":
        return 403, {"error": "the camera section works only on this computer"}
    return None


def _core(ctx, msg: dict, timeout: float):
    from ..cli import _ws_call
    return _ws_call(ctx.home, ctx.cfg.link.port, msg, "camera.view", timeout=timeout)


def offline_view(ctx) -> dict:
    """Core stopped: saved switch + sensorless probe. Opens nothing."""
    from ..features import Features
    from .probe import probe
    p = probe(ctx.cfg.camera.backend)
    enabled = Features(ctx.cfg, ctx.home).enabled("camera")
    return {"core_running": False, "state": "off", "enabled": enabled, "available": p.available,
            "device": {"detail": p.detail, "backend": p.backend, "name": p.backend}, "sensor_open": False,
            "indicator": False, "preview": {"active": False, "seconds_left": 0, "can_start": False,
                                            "refusal": "CORE_STOPPED", "max_seconds": 20},
            "data_flow": "local_only", "provider": {"vision_ready": False, "model": "", "photos_today": 0,
                                                    "daily_limit": ctx.cfg.camera.daily_cloud_photos},
            "last_result": None, "error": None}


def api_camera_status(ctx, m, body):
    r = _core(ctx, {"type": "camera.status", "payload": {}}, 4)
    if r is None:
        return 200, offline_view(ctx)
    return 200, {"core_running": True, **r}


def api_camera_test(ctx, m, body):
    refused = _local_only(ctx)
    if refused:
        return refused
    mode = (body or {}).get("mode")
    if mode not in ("probe", "frame"):
        return 400, {"error": "body needs {\"mode\": \"probe\"} or {\"mode\": \"frame\"}"}
    r = _core(ctx, {"type": "camera.test", "payload": {"mode": mode}}, 20)
    if r is None:
        if mode == "probe":
            v = offline_view(ctx)
            return 200, v | {"test": {"mode": "probe", "ok": v["available"], "opened_sensor": False,
                                      "detail": v["device"]["detail"]}}
        return 409, {"error": "JARVES is not running; start it first (jarvis start)", "core_running": False}
    return 200, {"core_running": True, **r}


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/camera/status", api_camera_status)
    register_route("POST", r"/api/camera/test", api_camera_test)
