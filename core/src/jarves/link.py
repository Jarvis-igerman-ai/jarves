"""Core <-> Godot face link: localhost-only WebSocket with a per-install token (no unauthenticated listener).

Envelope (both directions): {"type": "...", "payload": {...}, "cid": "optional correlation id"}.
First client message must be {"type": "hello", "payload": {"token": "...", "client": "godot"|"cli"}} within 3 s.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Awaitable, Callable

import websockets
from websockets.asyncio.server import ServerConnection, serve

from . import logs
from .health import Health

PROTOCOL = 1
Handler = Callable[[str, dict, "ServerConnection"], Awaitable[None]]


def load_token(path: Path) -> str:
    if not path.exists():
        path.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path.read_text(encoding="utf-8").strip()


class GodotLink:
    def __init__(self, host: str, port: int, token: str, health: Health, on_message: Handler,
                 on_face_connected: Callable[[], Awaitable[None]] | None = None) -> None:
        self.host, self.port, self.token = host, port, token
        self.health, self.on_message, self.on_face_connected = health, on_message, on_face_connected
        self.face: ServerConnection | None = None
        self.clients: set[ServerConnection] = set()
        self._server = None

    async def start(self) -> None:
        try:
            self._server = await serve(self._handle, self.host, self.port, ping_interval=5, ping_timeout=10,
                                       max_size=256 * 1024)
        except OSError as e:
            self.health.set("face_link", "unavailable", f"cannot listen on {self.host}:{self.port}: {e.strerror}",
                            "another Core may be running; stop it or change link.port")
            raise
        self.health.set("face_link", "unavailable", "waiting for the Godot face to connect", "start godot\\run_face.cmd")
        logs.info("link", "LINK_LISTENING", f"listening on ws://{self.host}:{self.port}")

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
            if msg.get("type") != "hello" or not secrets.compare_digest(str(p.get("token", "")), self.token):
                logs.warn("link", "LINK_AUTH_FAILED", "client rejected: bad or missing token")
                await ws.close(4401, "unauthorized")
                return
            client = p.get("client", "godot")
        except (asyncio.TimeoutError, ValueError, websockets.ConnectionClosed):
            logs.warn("link", "LINK_HANDSHAKE_FAILED", "client did not complete the hello handshake")
            await ws.close(4400, "handshake required")
            return
        self.clients.add(ws)
        if client == "godot":
            if self.face is not None and self.face is not ws:
                await self.face.close(4000, "replaced by a newer face client")
            self.face = ws
            self.health.set("face_link", "ok", f"Godot face connected (protocol {p.get('protocol', '?')})")
            logs.info("link", "LINK_FACE_CONNECTED", "Godot face connected")
        await self._send(ws, "hello.ok", {"protocol": PROTOCOL, "client": client})
        if client == "godot" and self.on_face_connected:
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
                try:
                    await self.on_message(t, payload, ws)
                except Exception:
                    logs.error("link", "LINK_HANDLER_ERROR", f"handler for {t} failed", exc=True)
                    await self._send(ws, "error", {"code": "CORE_INTERNAL", "message": f"{t} failed; see logs"})
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)
            if ws is self.face:
                self.face = None
                self.health.set("face_link", "unavailable", "Godot face disconnected",
                                "restart godot\\run_face.cmd; it reconnects automatically")
                logs.warn("link", "LINK_FACE_DISCONNECTED", f"Godot face disconnected ({peer})")

    @staticmethod
    async def _send(ws: ServerConnection, t: str, payload: dict) -> bool:
        try:
            await ws.send(json.dumps({"type": t, "payload": payload, "cid": logs.get_cid()}, ensure_ascii=False))
            return True
        except websockets.ConnectionClosed:
            return False

    async def send_face(self, t: str, payload: dict) -> bool:
        return await self._send(self.face, t, payload) if self.face else False

    async def reply(self, ws: ServerConnection, t: str, payload: dict) -> bool:
        return await self._send(ws, t, payload)

    async def broadcast(self, t: str, payload: dict) -> None:
        for ws in list(self.clients):
            await self._send(ws, t, payload)
