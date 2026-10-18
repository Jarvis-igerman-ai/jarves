"""Provider-neutral Music Skill + Pear Desktop (YouTube Music desktop app) adapter.

Pear Desktop plays through the official YouTube Music web player inside its own window, signed in by the user
(Premium or not). JARVES only calls Pear's local API Server plugin (http://127.0.0.1:26538): no cookies, no
stream extraction. Pairing uses a random client id that the user approves once in Pear's own dialog.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from .. import logs
from . import Skill, Tool, ToolResult


class MusicError(Exception):
    def __init__(self, code: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


@dataclass
class Track:
    video_id: str
    title: str
    subtitle: str = ""


@dataclass
class NowPlaying:
    title: str = ""
    artist: str = ""
    video_id: str = ""
    paused: bool = True
    elapsed_s: float = 0
    duration_s: float = 0


class MusicPlayer(Protocol):
    name: str

    async def search(self, query: str) -> list[Track]: ...
    async def play_track(self, track: Track) -> None: ...
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def next(self) -> None: ...
    async def set_volume(self, percent: int) -> None: ...
    async def now_playing(self) -> NowPlaying | None: ...


# ------------------------------------------------------------------ Pear Desktop
def _text(runs_holder: dict | None) -> str:
    if not runs_holder:
        return ""
    return "".join(r.get("text", "") for r in runs_holder.get("runs", []))


def parse_search(resp: dict, limit: int = 5) -> list[Track]:
    """Extract playable songs/videos from a YouTube Music search response (innertube JSON)."""
    out: list[Track] = []
    seen: set[str] = set()

    def add(vid: str | None, title: str, sub: str) -> None:
        if vid and vid not in seen and len(out) < limit:
            seen.add(vid)
            out.append(Track(vid, title or vid, sub))

    def walk(o) -> None:
        if isinstance(o, dict):
            card = o.get("musicCardShelfRenderer")
            if card:   # "top result"
                ep = (card.get("title", {}).get("runs") or [{}])[0].get("navigationEndpoint", {})
                add(ep.get("watchEndpoint", {}).get("videoId"), _text(card.get("title")), _text(card.get("subtitle")))
            item = o.get("musicResponsiveListItemRenderer")
            if item:
                vid = (item.get("playlistItemData") or {}).get("videoId")
                cols = item.get("flexColumns") or []
                txt = [_text(c.get("musicResponsiveListItemFlexColumnRenderer", {}).get("text")) for c in cols]
                add(vid, txt[0] if txt else "", " ".join(txt[1:2]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(resp)
    return out


class PearDesktopPlayer:
    name = "pear"

    def __init__(self, base_url: str, client_id_file: Path, timeout_s: float = 5.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.id_file = client_id_file
        self.timeout = timeout_s
        self._token: str | None = None
        self._client = httpx.AsyncClient(base_url=self.base, timeout=timeout_s, transport=transport)

    def client_id(self) -> str:
        if not self.id_file.exists():
            self.id_file.write_text("jarves-" + secrets.token_hex(16), encoding="utf-8")
        return self.id_file.read_text(encoding="utf-8").strip()

    async def close(self) -> None:
        await self._client.aclose()

    async def pair(self, wait_s: float = 60) -> None:
        """Request a token; on first use the user must click 'Allow' in Pear Desktop's dialog."""
        try:
            r = await self._client.post(f"/auth/{self.client_id()}", timeout=wait_s)
        except httpx.TransportError as e:
            raise MusicError("MUSIC_PLAYER_OFFLINE", "Pear Desktop is not reachable",
                             "start Pear Desktop and enable Plugins > API Server (host 127.0.0.1, port 26538)") from e
        if r.status_code == 403:
            raise MusicError("MUSIC_PAIR_DENIED", "pairing was denied in Pear Desktop", "run `jarves music pair` and click Allow")
        if r.status_code != 200:
            raise MusicError("MUSIC_PAIR_FAILED", f"pairing failed (HTTP {r.status_code})")
        self._token = r.json()["accessToken"]
        logs.info("music", "MUSIC_PAIRED", "paired with Pear Desktop API server")

    async def _req(self, method: str, path: str, json: dict | None = None, retry: bool = True) -> httpx.Response:
        if self._token is None:
            await self.pair(wait_s=self.timeout)
        try:
            r = await self._client.request(method, f"/api/v1{path}", json=json,
                                           headers={"Authorization": f"Bearer {self._token}"})
        except httpx.TimeoutException as e:
            raise MusicError("MUSIC_TIMEOUT", "Pear Desktop did not answer in time", "check that the app is not frozen") from e
        except httpx.TransportError as e:
            self._token = None
            raise MusicError("MUSIC_PLAYER_OFFLINE", "Pear Desktop is not reachable",
                             "start Pear Desktop (API Server plugin enabled)") from e
        if r.status_code in (401, 403) and retry:
            self._token = None      # app restarted with a new secret: re-pair (already-authorized id: no dialog)
            return await self._req(method, path, json, retry=False)
        if r.status_code >= 400:
            raise MusicError("MUSIC_PLAYER_ERROR", f"Pear Desktop returned HTTP {r.status_code}")
        return r

    async def search(self, query: str) -> list[Track]:
        r = await self._req("POST", "/search", {"query": query})
        return parse_search(r.json())

    async def play_track(self, track: Track) -> None:
        await self._req("POST", "/queue", {"videoId": track.video_id, "insertPosition": "INSERT_AFTER_CURRENT_VIDEO"})
        np = await self.now_playing()
        if np is None:                 # empty queue: the inserted track is the only one
            await self._req("PATCH", "/queue", {"index": 0})
        elif np.video_id != track.video_id:
            await self._req("POST", "/next")
        await self.resume()

    async def pause(self) -> None:
        await self._req("POST", "/pause")

    async def resume(self) -> None:
        await self._req("POST", "/play")

    async def next(self) -> None:
        await self._req("POST", "/next")

    async def set_volume(self, percent: int) -> None:
        await self._req("POST", "/volume", {"volume": percent})

    async def now_playing(self) -> NowPlaying | None:
        r = await self._req("GET", "/song")
        if r.status_code == 204 or not r.content:
            return None
        d = r.json()
        return NowPlaying(d.get("title", ""), d.get("artist", ""), d.get("videoId", ""), bool(d.get("isPaused", True)),
                          float(d.get("elapsedSeconds") or 0), float(d.get("songDuration") or 0))


# ------------------------------------------------------------------ fake player (tests only, never "real")
class FakePlayer:
    name = "fake"

    def __init__(self, catalog: list[Track] | None = None) -> None:
        self.catalog = catalog or [Track("v1", "Song One", "Artist A"), Track("v2", "Song Two", "Artist B")]
        self.np: NowPlaying | None = None
        self.volume = 50
        self.offline = False
        self.calls: list[str] = []

    def _chk(self, op: str) -> None:
        self.calls.append(op)
        if self.offline:
            raise MusicError("MUSIC_PLAYER_OFFLINE", "fake player offline", "start the player")

    async def search(self, query: str) -> list[Track]:
        self._chk("search")
        return [t for t in self.catalog if query.lower() in (t.title + " " + t.subtitle).lower()]

    async def play_track(self, track: Track) -> None:
        self._chk("play_track")
        self.np = NowPlaying(track.title, track.subtitle, track.video_id, False, 0, 200)

    async def pause(self) -> None:
        self._chk("pause")
        if self.np:
            self.np.paused = True

    async def resume(self) -> None:
        self._chk("resume")
        if self.np:
            self.np.paused = False

    async def next(self) -> None:
        self._chk("next")
        if self.np:
            others = [t for t in self.catalog if t.video_id != self.np.video_id]
            if others:
                await self.play_track(others[0])

    async def set_volume(self, percent: int) -> None:
        self._chk("set_volume")
        self.volume = percent

    async def now_playing(self) -> NowPlaying | None:
        self._chk("now_playing")
        return self.np


# ------------------------------------------------------------------ Skill
class PlayArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=200, description="Song, artist or 'artist song' to play")


class VolumeArgs(BaseModel):
    percent: int = Field(..., ge=0, le=100, description="Music volume 0-100")


class MusicSkill(Skill):
    name = "music"
    description = "Play music in the user's YouTube Music player (Pear Desktop)"

    def __init__(self, player: MusicPlayer, max_volume: int = 70, verify_tries: int = 8) -> None:
        self.player = player
        self.verify_tries = verify_tries
        self.max_volume = max_volume
        self.last_error: MusicError | None = None

    async def _guard(self, coro) -> ToolResult:
        try:
            res = await coro
            self.last_error = None
            return res
        except MusicError as e:
            self.last_error = e
            logs.warn("music", e.code, e.message, action=e.action)
            msg = {"MUSIC_PLAYER_OFFLINE": "נגן המוזיקה לא פועל. צריך להפעיל את Pear Desktop.",
                   "MUSIC_PAIR_DENIED": "החיבור לנגן נדחה. צריך לאשר אותו ב-Pear Desktop.",
                   "MUSIC_TIMEOUT": "נגן המוזיקה לא עונה."}.get(e.code, "יש בעיה בנגן המוזיקה.")
            return ToolResult(False, msg, {"action": e.action}, e.code)

    async def _verify_playing(self, video_id: str) -> NowPlaying | None:
        """Truthful status: report success only when the player itself says this track is playing."""
        np = None
        for _ in range(self.verify_tries):
            np = await self.player.now_playing()
            if np and np.video_id == video_id and not np.paused:
                return np
            await asyncio.sleep(0.5)
        return np if np and np.video_id == video_id and not np.paused else None

    def tools(self) -> list[Tool]:
        p = self.player

        async def play(a: PlayArgs):
            async def go():
                found = await p.search(a.query)
                if not found:
                    return ToolResult(False, f"לא מצאתי את \"{a.query}\".", code="MUSIC_NOT_FOUND")
                t = found[0]
                await p.play_track(t)
                np = await self._verify_playing(t.video_id)
                if np is None:
                    return ToolResult(False, "ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.",
                                      {"track": asdict(t)}, "MUSIC_NOT_CONFIRMED")
                return ToolResult(True, f"מנגן: {np.title} – {np.artist}", {"now_playing": asdict(np)})
            return await self._guard(go())

        def simple(op, ok_msg):
            async def run(_):
                async def go():
                    await getattr(p, op)()
                    return ToolResult(True, ok_msg)
                return await self._guard(go())
            return run

        async def volume(a: VolumeArgs):
            async def go():
                v = min(a.percent, self.max_volume)
                await p.set_volume(v)
                note = f" (מוגבל ל-{self.max_volume})" if v < a.percent else ""
                return ToolResult(True, f"עוצמת המוזיקה {v}{note}", {"volume": v})
            return await self._guard(go())

        async def status(_):
            async def go():
                np = await p.now_playing()
                if np is None or not np.title:
                    return ToolResult(True, "שום דבר לא מתנגן כרגע.", {"now_playing": None})
                state = "מושהה" if np.paused else "מתנגן"
                return ToolResult(True, f"{state}: {np.title} – {np.artist}", {"now_playing": asdict(np)})
            return await self._guard(go())

        return [
            Tool("music_play", "Search YouTube Music and start playing the best match (song/artist).", play,
                 PlayArgs, side_effect=True, timeout_s=25),
            Tool("music_pause", "Pause music.", simple("pause", "השהיתי."), side_effect=True),
            Tool("music_resume", "Resume music.", simple("resume", "ממשיך לנגן."), side_effect=True),
            Tool("music_next", "Skip to the next track.", simple("next", "השיר הבא."), side_effect=True),
            Tool("music_stop", "Stop music (pauses the player).", simple("pause", "עצרתי את המוזיקה."),
                 side_effect=True),
            Tool("music_volume", "Set the music volume (0-100).", volume, VolumeArgs, side_effect=True),
            Tool("music_status", "What is playing now.", status),
        ]

    async def health(self) -> tuple[str, str]:
        try:
            await self.player.now_playing()
            return "ok", f"{self.player.name} reachable"
        except MusicError as e:
            return "unavailable", f"{e.message}; {e.action}"
