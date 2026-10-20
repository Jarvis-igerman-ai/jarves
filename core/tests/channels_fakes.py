"""Local fake servers for the V3 P1 channel tests (real HTTP on 127.0.0.1, no mocks): Telegram Bot API, Home Assistant
REST API and Open-Meteo. Each records what it received so a test can assert exactly what left the PC."""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeServer:
    def __init__(self, handler) -> None:
        self.requests: list[dict] = []
        self.state: dict = {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = None
                req = {"method": method, "path": self.path, "body": body, "auth": self.headers.get("Authorization")}
                outer.requests.append(req)
                status, payload = handler(outer, req)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def paths(self, method: str | None = None) -> list[str]:
        return [r["path"] for r in self.requests if method is None or r["method"] == method]


# ---------------------------------------------------------------------- Telegram
GOOD_TOKEN = "123456789:AAH-fake_token_for_tests_0123456789abc"


def telegram_handler(srv: FakeServer, req: dict):
    m = re.match(r"^/bot([^/]+)/(\w+)$", req["path"])
    if not m or m.group(1) != GOOD_TOKEN:
        return 401, {"ok": False, "description": "Unauthorized"}
    method = m.group(2)
    if method == "getMe":
        return 200, {"ok": True, "result": {"id": 1, "is_bot": True, "username": "jarves_test_bot"}}
    if method == "getUpdates":
        return 200, {"ok": True, "result": srv.state.get("updates", [])}
    if method == "sendMessage":
        if srv.state.get("blocked"):
            return 403, {"ok": False, "description": "Forbidden: bot was blocked by the user"}
        return 200, {"ok": True, "result": {"message_id": 7}}
    return 404, {"ok": False}


def telegram_server(updates=None) -> FakeServer:
    s = FakeServer(telegram_handler)
    s.state["updates"] = updates if updates is not None else [
        {"update_id": 1, "message": {"chat": {"id": 555, "type": "private", "first_name": "Igal"},
                                     "from": {"first_name": "Igal"}, "text": "hi"}}]
    return s


# ---------------------------------------------------------------------- Home Assistant
HA_TOKEN = "ha-long-lived-token-for-tests"
HA_STATES = [
    {"entity_id": "light.living", "state": "off", "attributes": {"friendly_name": "Living room light"}},
    {"entity_id": "switch.heater", "state": "off", "attributes": {"friendly_name": "Heater"}},
    {"entity_id": "lock.front_door", "state": "locked", "attributes": {"friendly_name": "Front door"}},
    {"entity_id": "sensor.temp", "state": "21", "attributes": {"friendly_name": "Temp"}},
    {"entity_id": "light.secret", "state": "on", "attributes": {"friendly_name": "Not allowed light"}},
]


def ha_handler(srv: FakeServer, req: dict):
    if req["auth"] != f"Bearer {HA_TOKEN}":
        return 401, {"message": "401: Unauthorized"}
    p = req["path"]
    if p == "/api/":
        return 200, {"message": "API running."}
    if p == "/api/states":
        return 200, HA_STATES
    m = re.match(r"^/api/states/(.+)$", p)
    if m:
        s = next((x for x in HA_STATES if x["entity_id"] == m.group(1)), None)
        return (200, s) if s else (404, {"message": "Entity not found."})
    if re.match(r"^/api/services/\w+/\w+$", p) and req["method"] == "POST":
        return 200, []
    return 404, {}


def ha_server() -> FakeServer:
    return FakeServer(ha_handler)


# ---------------------------------------------------------------------- Open-Meteo
def weather_handler(srv: FakeServer, req: dict):
    if srv.state.get("down"):
        return 503, {"error": True}
    if req["path"].startswith("/v1/forecast"):
        return 200, {"current": {"temperature_2m": 26.4},
                     "daily": {"weather_code": [1], "temperature_2m_max": [29.2], "temperature_2m_min": [21.1],
                               "precipitation_probability_max": [10]}}
    return 404, {}


def weather_server() -> FakeServer:
    return FakeServer(weather_handler)
