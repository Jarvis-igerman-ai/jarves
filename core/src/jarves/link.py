"""Core <-> Godot face link: localhost-only WebSocket with a per-install token (no unauthenticated listener).

Envelope (both directions): {"type": "...", "payload": {...}, "cid": "optional correlation id"}.
First client message must be {"type": "hello", "payload": {"token": "...", "client": "godot"|"cli"}} within 3 s.

Roles (Issue #9 CR-08): after the hello the declared `client` maps to a role ("godot" -> face, "cli" -> cli);
anything else is closed with 4403. Each role may only send its allow-listed message types; a known type sent by
the wrong role gets error LINK_FORBIDDEN and is never dispatched. The dispatcher receives the sending `Client`
(`.id`, `.role`, `.ws`), so the Core can bind a confirmation to the face connection that was asked
(`face_client_id()`).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import websockets
from websockets.asyncio.server import ServerConnection, serve

from . import logs
from .health import Health

PROTOCOL = 1

ROLES = {"godot": "face", "cli": "cli"}
# Device events (keep in sync with deviceport.INBOUND; a test asserts it).
DEVICE_EVENTS = frozenset({"button.down", "button.up", "mic.mute_changed", "presence.changed", "light.changed",
                           "network.changed"})
CLOSE_GRACE_S = 10.0       # a face disconnect this soon after a requested close is the close itself, not a fault
ALLOWED: dict[str, frozenset[str]] = {
    "face": frozenset({"talk.down", "talk.up", "user.text", "mic.set_mute", "status.request", "confirm.reply",
                       "debug.enable", "desktop.close", "ui.open_dashboard",
                       # privacy strip: "What does he see?" preview, camera switch (on = asks on screen first)
                       "camera.preview", "camera.set_enabled"}) | DEVICE_EVENTS,
    # runtime.set (M1): show/hide the window, software mute, pause alerts - the CLI, dashboard and tray route;
    # camera.preview: the dashboard Home preview (each poll keeps it open a few seconds and returns the last frame)
    "cli": frozenset({"status.request", "core.shutdown", "debug.enable", "runtime.set", "camera.preview",
                      "camera.status", "camera.test",       # dashboard Camera section / `jarvis camera`
                      "ui.open_dashboard",                 # `jarvis web`: open the Chat page of the running Core
                      # terminal text chat (chat_service.py): `jarvis chat`
                      "talk.start", "talk.stop",           # `jarvis talk`: live conversation without the wake word
                      "chat.hello", "chat.turn", "chat.cancel", "chat.command", "chat.approval_answer"}),
}
KNOWN_TYPES = frozenset().union(*ALLOWED.values())


@dataclass(eq=False)
class Client:
    id: str                    # random per connection; never reused
    role: str                  # "face" | "cli"
    ws: ServerConnection


# New dispatcher signature: on_message(msg, client) with msg = {"type": str, "payload": dict, "cid": str|None}.
# The legacy (type, payload, ws) signature is still accepted (detected by arity).
Handler = Callable[..., Awaitable[None]]


def load_token(path: Path) -> str:
    if not path.exists():
        path.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path.read_text(encoding="utf-8").strip()


def _arity(fn: Callable) -> int:
    try:
        ps = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return 3
    return sum(1 for p in ps if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))


FACE_HIDDEN_DETAIL = "face hidden: shown on request"
FACE_HIDDEN_ACTION = "jarvis show"


class GodotLink:
    def __init__(self, host: str, port: int, token: str, health: Health, on_message: Handler,
                 on_face_connected: Callable[[], Awaitable[None]] | None = None) -> None:
        self.host, self.port, self.token = host, port, token
        self.health, self.on_message, self.on_face_connected = health, on_message, on_face_connected
        self._legacy = _arity(on_message) >= 3
        self.face: ServerConnection | None = None
        self.face_client: Client | None = None
        self.clients: set[ServerConnection] = set()
        self._server = None
        self._close_expected_until = 0.0   # a close the Core asked for / the user chose (not a fault)
        self.on_client_closed: Callable[[Client], None] | None = None   # a text-chat terminal went away
        # Runtime sets this: False while no window is wanted (hidden / background mode). A missing face is then idle
        # ("shown on request"), not a fault, and must not degrade the Core.
        self.face_expected: Callable[[], bool] | None = None

    def wants_face(self) -> bool:
        try:
            return True if self.face_expected is None else bool(self.face_expected())
        except Exception:
            return True

    def set_face_idle(self) -> None:
        """No face and none expected: report it as idle (status "disabled" never degrades the Core)."""
        self.health.set("face_link", "disabled", FACE_HIDDEN_DETAIL, FACE_HIDDEN_ACTION)

    def refresh_face_health(self) -> None:
        """Re-derive face_link after the Runtime's wish changed (hide / show / window closed). A connected face wins."""
        if self.face is not None or self._server is None:
            return
        cur = self.health.components.get("face_link")
        if not self.wants_face():
            self.set_face_idle()
        elif cur is not None and cur.status == "disabled":
            self.health.set("face_link", "unknown", "face window starting", "")

    def face_client_id(self) -> str | None:
        """Id of the currently connected face (bind confirm.request/confirm.reply to it); None if no face."""
        return self.face_client.id if self.face_client else None

    async def start(self) -> None:
        try:
            self._server = await serve(self._handle, self.host, self.port, ping_interval=5, ping_timeout=10,
                                       max_size=256 * 1024)
        except OSError as e:
            self.health.set("face_link", "unavailable", f"cannot listen on {self.host}:{self.port}: {e.strerror}",
                            "another Core may be running; stop it or change link.port")
            raise
        if self.wants_face():
            self.health.set("face_link", "unavailable", "waiting for the Godot face to connect",
                            "start godot\\run_face.cmd")
        else:
            self.set_face_idle()
        logs.info("link", "LINK_LISTENING", "link listening", bind_host=self.host, port=self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, ws: ServerConnection) -> None:
        peer = "local"
        try:
            raw = await asyncio.wait_for(ws.recv(), 3)
            msg = json.loads(raw)
            p = msg.get("payload") or {}
            if not isinstance(p, dict):
                raise ValueError("payload must be an object")
            if msg.get("type") != "hello" or not secrets.compare_digest(str(p.get("token", "")), self.token):
                logs.warn("link", "LINK_AUTH_FAILED", "client rejected: bad or missing token")
                await ws.close(4401, "unauthorized")
                return
            role = ROLES.get(str(p.get("client", "")))
        except (asyncio.TimeoutError, ValueError, AttributeError, websockets.ConnectionClosed):
            logs.warn("link", "LINK_HANDSHAKE_FAILED", "client did not complete the hello handshake")
            await ws.close(4400, "handshake required")
            return
        if role is None:
            logs.warn("link", "LINK_ROLE_REJECTED", "client rejected: unknown client type")
            await ws.close(4403, "unknown client")
            return
        client = Client(secrets.token_hex(8), role, ws)
        self.clients.add(ws)
        if role == "face":
            if self.face is not None and self.face is not ws:
                await self.face.close(4000, "replaced by a newer face client")
            self.face, self.face_client = ws, client
            self.health.set("face_link", "ok", f"Godot face connected (protocol {p.get('protocol', '?')})")
            logs.info("link", "LINK_FACE_CONNECTED", "Godot face connected")
        await self._send(ws, "hello.ok", {"protocol": PROTOCOL, "client": str(p.get("client")), "role": role})
        if role == "face" and self.on_face_connected:
            await self.on_face_connected()
        try:
            async for raw in ws:
                try:
                    m = json.loads(raw)
                    t = str(m["type"])
                    payload = m.get("payload") or {}
                    if not isinstance(payload, dict):
                        raise ValueError("payload must be an object")
                except (ValueError, KeyError, TypeError):
                    await self._send(ws, "error", {"code": "LINK_BAD_MESSAGE", "message": "invalid message"})
                    continue
                if m.get("cid"):
                    logs.set_cid(str(m["cid"])[:32])
                if t == "desktop.close" and role == "face":
                    self.expect_close()                  # the user closed the window: its disconnect follows
                if t not in KNOWN_TYPES:
                    await self._send(ws, "error", {"code": "LINK_UNKNOWN_TYPE",
                                                   "message": f"unknown message type {t[:64]}"})
                    continue
                if t not in ALLOWED[role]:
                    logs.warn("link", "LINK_FORBIDDEN", "client may not send this message type", msg_type=t, role=role)
                    await self._send(ws, "error", {"code": "LINK_FORBIDDEN",
                                                   "message": f"{t} is not allowed for this client"})
                    continue
                try:
                    if self._legacy:
                        await self.on_message(t, payload, ws)
                    else:
                        await self.on_message({"type": t, "payload": payload, "cid": m.get("cid")}, client)
                except Exception:
                    logs.error("link", "LINK_HANDLER_ERROR", "link handler failed", msg_type=t, exc=True)
                    await self._send(ws, "error", {"code": "CORE_INTERNAL", "message": f"{t} failed; see logs"})
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)
            if role == "cli" and self.on_client_closed is not None:
                try:
                    self.on_client_closed(client)
                except Exception:
                    logs.error("link", "LINK_CLOSE_HOOK_FAILED", "cli close hook failed", exc=True)
            if ws is self.face:
                self.face, self.face_client = None, None
                if time.monotonic() < self._close_expected_until:
                    # SW-V3 P0.1 (review Q13): hide / window closed / Core stopping logged a WARNING and became a
                    # dashboard issue every time; a requested close is normal, only an unexpected loss is a fault
                    self.health.set("face_link", "disabled", "display window closed", FACE_HIDDEN_ACTION)
                    logs.info("link", "LINK_FACE_CLOSED", "Godot face closed as requested", peer=peer)
                elif not self.wants_face():
                    self.set_face_idle()
                    logs.info("link", "LINK_FACE_CLOSED", "Godot face closed (none wanted)", peer=peer)
                else:
                    self.health.set("face_link", "unavailable", "Godot face disconnected",
                                    "restart godot\\run_face.cmd; it reconnects automatically")
                    logs.info("link", "LINK_FACE_DISCONNECTED", "Godot face disconnected", peer=peer)

    def expect_close(self, seconds: float = CLOSE_GRACE_S) -> None:
        """The face is about to close on purpose (hide, window closed by the user, Core stopping)."""
        self._close_expected_until = time.monotonic() + seconds

    @staticmethod
    async def _send(ws: ServerConnection, t: str, payload: dict) -> bool:
        if t != "mic.level":
            logs.verbose("link", "MSG_OUT", "link message out", msg_type=t, keys=sorted(payload)[:8])
        try:
            await ws.send(json.dumps({"type": t, "payload": payload, "cid": logs.get_cid()}, ensure_ascii=False))
            return True
        except websockets.ConnectionClosed:
            return False

    async def send_face(self, t: str, payload: dict) -> bool:
        if t == "display.close":
            self.expect_close()
        return await self._send(self.face, t, payload) if self.face else False

    async def reply(self, to: ServerConnection | Client, t: str, payload: dict) -> bool:
        return await self._send(to.ws if isinstance(to, Client) else to, t, payload)

    async def broadcast(self, t: str, payload: dict) -> None:
        for ws in list(self.clients):
            await self._send(ws, t, payload)
