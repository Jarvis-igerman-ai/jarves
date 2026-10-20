"""Shared fakes for the OpenAI Responses streaming API (SW-V4 M: consolidated from test_camera.py and test_tasks.py,
behaviour unchanged). Offline: no network, no key."""
from __future__ import annotations

import asyncio


class FakeStream:
    """An async-iterable event stream. `hang=True` stops after the events and never ends (a stalled stream)."""

    def __init__(self, events, hang=False):
        self.events, self.hang, self.closed = events, hang, False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self.events:
            yield e
        if self.hang:
            await asyncio.sleep(30)

    async def close(self):
        self.closed = True


class FakeResponses:
    """`client.responses` that hands out the scripted streams in order and records every request body."""

    def __init__(self, streams):
        self.streams, self.bodies = list(streams), []

    async def create(self, **body):
        self.bodies.append(body)
        return self.streams.pop(0)


class TextResponses:
    """`client.responses` that always answers one text (a vision/description reply) with fixed usage."""

    def __init__(self, text="זה ספל קפה לבן."):
        self.text, self.bodies = text, []

    async def create(self, **body):
        self.bodies.append(body)
        return FakeStream([{"type": "response.created", "response": {"id": "r1", "model": body["model"]}},
                           {"type": "response.output_text.delta", "delta": self.text},
                           {"type": "response.completed", "response": {"usage": {"input_tokens": 900,
                                                                                 "output_tokens": 20}}}])


# ---------------------------------------------------------------------------------------------- SW-V4 D (#14)
class FakeLiveConn:
    """A scripted GPT-Live WebSocket (the SDK `client.live.connect()` object): it validates what the Core sends the way
    the real server did in the recorded session (tests/fixtures/live_client_delegation_events.json): a client
    delegation is announced with metadata only, appends must carry a KNOWN delegation id or null, the result is
    "spoken" back as output-transcript deltas. Events are plain dicts (LiveVoice reads dicts and SDK models alike)."""

    def __init__(self, server: "FakeLiveServer") -> None:
        self.srv = server
        self.q: asyncio.Queue = asyncio.Queue()
        self.config: dict = {}
        self.sent: list[tuple[str, dict]] = []
        self.delegations: set[str] = set()
        self.closed = False
        conn = self

        class _Append:
            def __init__(self, kind):
                self.kind = kind

            async def append(self, content, delegation_id=None, event_id=None):
                conn.sent.append((self.kind, {"content": content, "delegation_id": delegation_id, "event_id": event_id}))
                if delegation_id is not None and (delegation_id not in conn.delegations or conn.srv.reject_appends):
                    conn.emit({"type": "error", "error": {"code": "invalid_value", "type": "invalid_request_error",
                                                          "message": "unknown client delegation id",
                                                          "client_event_id": event_id}})
                    return
                conn.emit({"type": f"session.{self.kind}.appended", "client_event_id": event_id})
                if self.kind == "commentary" and not conn.srv.mute:
                    conn.srv.spoken.append(content)
                    conn.emit({"type": "session.output_transcript.delta", "delta": content[:120]})

        class _Audio:
            async def append(self, audio):
                pass

        class _Session:
            commentary, thinking, instructions = _Append("commentary"), _Append("thinking"), _Append("instructions")
            input_audio = _Audio()

            async def start(self, session, event_id=None):
                conn.config = session
                conn.srv.configs.append(session)
                if conn.srv.start_error:
                    conn.emit({"type": "error", "error": {"code": "invalid_value", "message": conn.srv.start_error,
                                                          "client_event_id": event_id}})
                conn.emit({"type": "session.started", "session": {"id": "live_fake", "model": session.get("model")}})

            async def close(self, event_id=None):
                conn.emit({"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 3.0}})
                conn.closed = True

        class _Item:
            async def create(self, item, event_id=None):
                conn.sent.append(("response.item.create", item))

        class _Resp:
            item = _Item()

            async def create(self, event_id=None):
                conn.sent.append(("response.create", {"event_id": event_id}))
        self.session, self.response = _Session(), _Resp()

    def emit(self, ev: dict) -> None:
        self.q.put_nowait(ev)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        while True:
            ev = await self.q.get()
            yield ev
            if ev.get("type") == "session.closed":
                return

    def user_says(self, text: str, *, delegate: bool = True) -> str:
        """The user speaks: input-transcript deltas, then (as GPT-Live did live) a delegation ~0.4 s later."""
        for w in text.split(" "):
            self.emit({"type": "session.input_transcript.delta", "delta": " " + w})
        if not delegate:
            return ""
        self.srv.n += 1
        did = f"item_fake{self.srv.n}"
        dtype = (self.config.get("delegation") or {}).get("type")
        if dtype == "client":
            self.delegations.add(did)
            self.emit({"type": "session.delegation.created", "event_id": f"ev{self.srv.n}", "offset_ms": 1000,
                       "delegation": {"id": did, "type": "delegation", "target": "client"}})
        elif dtype == "responses":
            self.emit({"type": "session.delegation.created", "event_id": f"ev{self.srv.n}", "offset_ms": 1000,
                       "delegation": {"id": did, "type": "delegation", "target": "responses", "response_id": "r1"}})
        return did


class FakeLiveServer:
    """`client` for LiveVoice(client=...): `client.live.connect()` hands out FakeLiveConn objects."""

    def __init__(self, *, start_error: str = "", reject_appends: bool = False, mute: bool = False) -> None:
        self.mute = mute                    # GPT-Live does not say appended results (it dropped one live, 20 Oct)
        self.conns: list[FakeLiveConn] = []
        self.configs: list[dict] = []
        self.spoken: list[str] = []
        self.n = 0
        self.start_error, self.reject_appends = start_error, reject_appends
        srv = self

        class _CM:
            async def __aenter__(self):
                c = FakeLiveConn(srv)
                srv.conns.append(c)
                return c

            async def __aexit__(self, *a):
                return False

        class _Live:
            def connect(self, **_):
                return _CM()
        self.live = _Live()

    @property
    def conn(self) -> FakeLiveConn:
        return self.conns[-1]


class ResponsesHTTPServer:
    """A REAL local HTTP server speaking the Responses streaming API (SSE) for the real openai SDK
    (`openai.AsyncOpenAI(base_url=srv.url, api_key="test-not-real")`). Each POST /v1/responses pops a script:
    {"text", "calls": [function calls], "searches", "usage", "delay_s" (before the first event, on a real clock: a slow
    backend), "drop" (close mid-stream), "status" (HTTP error code), "gate" (threading.Event to wait for)}.
    Records every request body and the count of requests (to prove a refused request was never sent)."""

    def __init__(self, scripts=None, default=None) -> None:
        import http.server
        import json as _json
        import threading
        import time as _time
        self.scripts, self.bodies = list(scripts or []), []
        self.default = default or {"text": "בתל אביב עכשיו 24 מעלות."}
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                body = _json.loads(self.rfile.read(n) or b"{}")
                outer.bodies.append(body)
                s = outer.scripts.pop(0) if outer.scripts else dict(outer.default)
                if s.get("status"):
                    data = _json.dumps({"error": {"code": s.get("code", "server_error"), "message": "fake"}}).encode()
                    self.send_response(int(s["status"]))
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("connection", "close")
                self.end_headers()
                rid = f"resp_{len(outer.bodies)}"

                def ev(e):
                    self.wfile.write(f"event: {e['type']}\ndata: {_json.dumps(e, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                try:
                    ev({"type": "response.created", "sequence_number": 0,
                        "response": {"id": rid, "object": "response", "status": "in_progress", "model": body.get("model"),
                                     "output": [], "created_at": 0}})
                    if s.get("gate") is not None:
                        s["gate"].wait(30)
                    if s.get("delay_s"):
                        _time.sleep(s["delay_s"])
                    out, i = [], 0
                    for k in range(int(s.get("searches", 0))):
                        item = {"type": "web_search_call", "id": f"ws{k}", "status": "completed",
                                "action": {"type": "search", "query": "q"}}
                        ev({"type": "response.output_item.done", "output_index": i, "item": item, "sequence_number": i + 1})
                        out.append(item)
                        i += 1
                    if s.get("drop"):
                        self.wfile.flush()
                        self.connection.shutdown(2)
                        return
                    for c in s.get("calls", []):
                        item = {"type": "function_call", "id": f"fc{i}", "call_id": c.get("call_id", f"call{i}"),
                                "name": c["name"], "arguments": c.get("arguments", "{}"), "status": "completed"}
                        ev({"type": "response.output_item.done", "output_index": i, "item": item, "sequence_number": i + 1})
                        out.append(item)
                        i += 1
                    if not s.get("calls"):
                        text = s.get("text", "")
                        ev({"type": "response.output_text.delta", "delta": text, "output_index": i, "item_id": "m",
                            "content_index": 0, "sequence_number": i + 1, "logprobs": []})
                        item = {"type": "message", "id": "m", "role": "assistant", "status": "completed",
                                "content": [{"type": "output_text", "text": text, "annotations": []}]}
                        ev({"type": "response.output_item.done", "output_index": i, "item": item, "sequence_number": i + 2})
                        out.append(item)
                    u = s.get("usage") or {"input_tokens": 1200, "output_tokens": 30}
                    ev({"type": "response.completed", "sequence_number": i + 3, "response": {
                        "id": rid, "object": "response", "status": "completed", "model": body.get("model"),
                        "created_at": 0, "output": out, "service_tier": body.get("service_tier") or "default",
                        "usage": {"input_tokens": u["input_tokens"], "output_tokens": u["output_tokens"],
                                  "total_tokens": u["input_tokens"] + u["output_tokens"],
                                  "input_tokens_details": {"cached_tokens": 0},
                                  "output_tokens_details": {"reasoning_tokens": 0}}}})
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def client(self):
        import openai
        return openai.AsyncOpenAI(base_url=self.url, api_key="test-not-real")

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
