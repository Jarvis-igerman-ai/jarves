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
        self.polled = threading.Event()                  # set by the Telegram fake on every getUpdates
        self.wake = threading.Event()                    # releases a blocked long poll
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

    def deliver(self, update: dict) -> None:
        """A message arrives at the (Telegram) fake while a long poll may be waiting."""
        self.state.setdefault("updates", []).append(update)
        self.wake.set()

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
        body = req["body"] or {}
        srv.polled.set()                                  # a test waits for this instead of sleeping
        if body.get("offset") is not None:               # Telegram semantics: confirmed updates are dropped
            srv.state["updates"] = [u for u in srv.state.get("updates", []) if u["update_id"] >= body["offset"]]
        if not srv.state.get("updates") and body.get("timeout") and srv.state.get("block"):
            srv.wake.wait(10)                             # long poll: returns the moment a test calls srv.deliver()
            srv.wake.clear()
        return 200, {"ok": True, "result": list(srv.state.get("updates", []))}
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


# ---------------------------------------------------------------------- Instagram Graph API
IG_TOKEN = "IGAAfake_instagram_token_for_tests_0123456789"
IG_SECRET = "app-secret-for-tests"
IG_ME = {"user_id": "9001", "username": "igal_biz", "account_type": "BUSINESS"}


def ig_handler(srv: FakeServer, req: dict):
    if req["auth"] != f"Bearer {IG_TOKEN}":
        return 401, {"error": {"message": "Invalid OAuth access token", "code": 190}}
    p = req["path"].split("?")[0]
    if p == "/me":
        return 200, srv.state.get("me", IG_ME)
    if p == "/me/conversations":
        return 200, {"data": [{"id": "c1", "messages": {"data": srv.state.get("messages", [])}}]}
    if p == "/me/messages":
        if srv.state.get("window_closed"):
            return 400, {"error": {"message": "outside of allowed window", "code": 10, "error_subcode": 2534022}}
        return 200, {"recipient_id": req["body"]["recipient"]["id"], "message_id": "m_sent"}
    if p == "/refresh_access_token":
        return 200, {"access_token": IG_TOKEN, "token_type": "bearer", "expires_in": 5184000}
    return 404, {"error": {"code": 100}}


def instagram_server(messages=None) -> FakeServer:
    s = FakeServer(ig_handler)
    s.state["messages"] = messages if messages is not None else []
    return s


# ---------------------------------------------------------------------- Pear Desktop API Server plugin
def pear_handler(srv: FakeServer, req: dict):
    p = req["path"]
    if p.startswith("/auth/"):
        return (403, {}) if srv.state.get("deny") else (200, {"accessToken": "pear-token"})
    if req["auth"] != "Bearer pear-token":
        return 401, {}                                   # the server is up, the client is not authorised yet
    return 200, {"title": "Song", "artist": "Artist"}


def pear_server() -> FakeServer:
    return FakeServer(pear_handler)


# ---------------------------------------------------------------------- clipboard (the interface jarves.connect uses)
class FakeClipboard:
    """Implements connect.Clipboard. `script` = what successive reads return (the last value stays); every write and
    clear is recorded so a test can assert the token was cleared after use and nothing else was touched."""

    def __init__(self, script: list[str] | None = None) -> None:
        self.script = list(script or [])
        self.value = ""
        self.reads = 0
        self.writes: list[str] = []
        self.cleared = 0

    def read(self) -> str | None:
        self.reads += 1
        if self.script:
            self.value = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return self.value

    def write(self, text: str) -> bool:
        self.writes.append(text)
        self.value = text
        return True

    def clear(self) -> bool:
        self.cleared += 1
        self.value = ""
        self.script = []
        return True
