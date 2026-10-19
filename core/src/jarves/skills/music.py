"""Provider-neutral Music Skill + Pear Desktop (YouTube Music desktop app) adapter.

Pear Desktop plays through the official YouTube Music web player inside its own window, signed in by the user
(Premium or not). JARVES only calls Pear's local API Server plugin (http://127.0.0.1:26538): no cookies, no
stream extraction. Pairing uses a random client id that the user approves once in Pear's own dialog.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
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


# ------------------------------------------------------------------ title matching
_BIDI = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮⁦⁧⁨⁩﻿"), None)
_BRACKETS = re.compile(r"[(\[][^)\]]*[)\]]")
_DASH_TAIL = re.compile(r"\s+[-–—]\s+.*\b(?:remaster(?:ed)?|version|live|edit|mix|mono|stereo|official|video|audio)\b.*$")


def clean_title(s: str | None) -> str:
    """Casefolded, whitespace-collapsed, without bidi control marks (Hebrew titles often carry U+200F)."""
    return " ".join((s or "").translate(_BIDI).casefold().split())


def base_title(s: str | None) -> str:
    """The song itself: '(Official Video)', '[Official Music Video]', '(2013 Remaster)', '| Lyrics', ' - Remastered
    2011' removed. YouTube Music plays a searched music video as its 'song' counterpart, whose title lacks these."""
    t = clean_title(s).split(" | ")[0]
    t = _DASH_TAIL.sub("", _BRACKETS.sub(" ", t))
    return " ".join(t.split()).strip(" -–—")


def _num(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _json(r: httpx.Response):
    """Body as JSON; None when empty; an honest MusicError (not a crash) when Pear sends something unreadable."""
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError as e:
        raise MusicError("MUSIC_PLAYER_ERROR", "Pear Desktop sent an unreadable answer",
                         "update or restart Pear Desktop") from e


# ------------------------------------------------------------------ Pear Desktop
def _text(runs_holder: dict | None) -> str:
    if not runs_holder:
        return ""
    return "".join(r.get("text", "") for r in runs_holder.get("runs", []))


def parse_queue(resp: dict) -> list[tuple[str, bool]]:
    """Pear GET /api/v1/queue -> [(videoId, selected)]. Items are playlistPanelVideoRenderer, optionally wrapped
    in playlistPanelVideoWrapperRenderer.primaryRenderer (shape checked on Pear 3.12.0, 18 Oct 2026)."""
    out = []
    items = resp.get("items") if isinstance(resp, dict) else None
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        r = it.get("playlistPanelVideoRenderer") or \
            ((it.get("playlistPanelVideoWrapperRenderer") or {}).get("primaryRenderer") or {}).get("playlistPanelVideoRenderer")
        if r and r.get("videoId"):
            out.append((r["videoId"], bool(r.get("selected"))))
    return out


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


def _display_text(resp) -> str:
    """All visible strings ('text' runs) of an innertube response - never endpoint params, which echo the query.
    Iterative and joined once (a recursive join per level took 8 s on a real 250 KB answer, QA 19 Oct)."""
    out: list[str] = []
    stack = [resp]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "text" and isinstance(v, str):
                    out.append(v)
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(o, list):
            stack.extend(o)
    return clean_title(" ".join(out))


def search_is_relevant(resp, query: str) -> bool:
    """YouTube Music answers ANY query, even gibberish, with something (QA 19 Oct, real Pear: "asdkjhqwe zzxxcc"
    -> a random 2.7K-view user video, then generic hits). Accept the results only when at least one word of the
    query appears somewhere in what YouTube shows (titles, artists, cards, "did you mean"). Hebrew and mood
    queries pass ("משהו רגוע" -> "משהו קטן וטוב", "שלום חנוך" -> the artist card)."""
    words = [w for w in re.findall(r"\w+", clean_title(query)) if len(w) >= 2]
    if not words:
        return True
    shown = _display_text(resp)
    return any(w in shown for w in words)


class PearDesktopPlayer:
    name = "pear"

    def __init__(self, base_url: str, client_id_file: Path, timeout_s: float = 5.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.id_file = client_id_file
        self.timeout = timeout_s
        self._token: str | None = None
        self._qlock = asyncio.Lock()
        self._waiters = 0
        self._client = httpx.AsyncClient(base_url=self.base, timeout=timeout_s, transport=transport)

    def client_id(self) -> str:
        if not self.id_file.exists():
            self.id_file.write_text("jarves-" + secrets.token_hex(16), encoding="utf-8")
        return self.id_file.read_text(encoding="utf-8").strip()

    async def close(self) -> None:
        await self._client.aclose()

    async def trim_history(self, keep_before: int = 3, max_deletes: int = 12) -> int:
        """Drop PLAYED history from Pear's queue so it stays small. Found by the music campaign (19 Oct 2026): every
        play inserts a song and nothing ever removed it; ~250 plays left a 207-item queue whose every read was 3.3 MB,
        which made Pear (and next/confirm) slow. DELETE /queue/0 removes the oldest item; the playing song stays
        selected (verified on Pear 3.12). Only items BEFORE the current one are touched; never the current or upcoming.
        Small paced batches on purpose: a burst of ~170 deletes on a 200-item queue wedged Pear's player for minutes
        (19 Oct), while 12 per call (every 6th play) keeps a normal queue small without ever stressing it.
        Returns how many items were removed."""
        async with self._qlock:
            return await self._trim(keep_before, max_deletes)

    async def _trim(self, keep_before: int, max_deletes: int) -> int:
        q = await self.queue()
        cur = next((i for i, (_, sel) in enumerate(q) if sel), -1)
        n = min(max(0, cur - keep_before), max_deletes)
        done = 0
        for _ in range(n):
            if self._waiters:                      # a play/next is waiting: give way at once
                break
            r = await self._req("DELETE", "/queue/0")
            if r.status_code >= 400:
                break
            done += 1
            await asyncio.sleep(0.15)
        return done

    @property
    def _paired_marker(self) -> Path:
        return self.id_file.with_name(self.id_file.name + ".paired")

    async def pair(self, wait_s: float = 60) -> None:
        """Explicit, user-initiated pairing (`jarves music pair`): Pear Desktop shows an Allow/Deny dialog the
        first time; the user's click is the authorization. Never called automatically for an unpaired client."""
        try:
            r = await self._client.post(f"/auth/{self.client_id()}", timeout=wait_s)
        except httpx.TransportError as e:
            raise MusicError("MUSIC_PLAYER_OFFLINE", "Pear Desktop is not reachable",
                             "start Pear Desktop and enable Plugins > API Server (host 127.0.0.1, port 26538)") from e
        if r.status_code == 403:
            self._paired_marker.unlink(missing_ok=True)
            raise MusicError("MUSIC_PAIR_DENIED", "pairing was denied in Pear Desktop", "run `jarves music pair` and click Allow")
        if r.status_code != 200:
            raise MusicError("MUSIC_PAIR_FAILED", f"pairing failed (HTTP {r.status_code})")
        self._token = r.json()["accessToken"]
        self._paired_marker.write_text("paired by user approval in Pear Desktop\n", encoding="utf-8")
        logs.info("music", "MUSIC_PAIRED", "paired with Pear Desktop API server")

    async def _ensure_token(self) -> None:
        if self._token is not None:
            return
        if not self._paired_marker.exists():
            # never pop Pear's approval dialog from background/automatic calls
            if not await self.reachable():
                raise MusicError("MUSIC_PLAYER_OFFLINE", "Pear Desktop is not reachable",
                                 "start Pear Desktop and enable Plugins > API Server (host 127.0.0.1, port 26538)")
            raise MusicError("MUSIC_NOT_PAIRED", "JARVES is not paired with Pear Desktop yet",
                             "run `jarves music pair` once and click Allow in Pear Desktop")
        await self.pair(wait_s=self.timeout)    # already-approved id: Pear returns a token without a dialog

    async def reachable(self) -> bool:
        try:
            await self._client.get("/api/v1/song")     # 401 without a token still proves the server is up
            return True
        except httpx.TransportError:
            return False

    async def _req(self, method: str, path: str, json: dict | None = None, retry: bool = True) -> httpx.Response:
        await self._ensure_token()
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
        d = _json(r)
        if not isinstance(d, (dict, list)):
            return []
        tracks = parse_search(d)
        if tracks and not search_is_relevant(d, query):
            logs.info("music", "MUSIC_SEARCH_IRRELEVANT", "search results do not mention the query; treated as not found",
                      results=len(tracks))
            return []
        return tracks

    async def queue(self) -> list[tuple[str, bool]]:
        """Current Pear queue as (videoId, selected) in order; [] when it cannot be read (play_track falls back)."""
        r = await self._req("GET", "/queue")
        if r.status_code != 200:
            return []
        try:
            return parse_queue(_json(r))
        except MusicError:
            return []

    @contextlib.asynccontextmanager
    async def _exclusive(self):
        """Index-based queue operations (play_track, next) and the background trim must never overlap: deleting items
        shifts the positions a PATCH /queue jump relies on (real Pear, 19 Oct: a play right after a trim failed)."""
        self._waiters += 1
        try:
            async with self._qlock:
                yield
        finally:
            self._waiters -= 1

    async def play_track(self, track: Track, wait_s: float = 3.0) -> None:
        async with self._exclusive():
            await self._play_track(track, wait_s)

    async def _play_track(self, track: Track, wait_s: float = 3.0) -> None:
        """Insert after the current song, then jump to THAT queue index. QA 18 Oct on real Pear 3.12: the insert
        lands asynchronously, so pressing "next" right away skipped to the OLD next song; we wait for the
        inserted item to appear and select it by index instead.
        QA 19 Oct: asking for the same song again inserted it again, so the queue filled with back-to-back copies
        and "next" then landed on the same song. The current song is reused (checked with the cheap /song: the
        queue answer grows to megabytes on a long queue); copies further on are skipped by next()."""
        np = await self.now_playing()
        if np is not None and np.video_id == track.video_id:
            await self.resume()
            return
        await self._req("POST", "/queue", {"videoId": track.video_id, "insertPosition": "INSERT_AFTER_CURRENT_VIDEO"})
        deadline = asyncio.get_running_loop().time() + wait_s
        idx = None
        while True:
            q = await self.queue()
            cur = next((i for i, (_, sel) in enumerate(q) if sel), -1)
            after = [i for i, (vid, _) in enumerate(q) if vid == track.video_id and i > cur]
            idx = after[0] if after else next((i for i, (vid, _) in enumerate(q) if vid == track.video_id), None)
            if idx is not None or asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(0.2)
        if idx is not None:
            await self._select(idx, track.video_id)
        else:                          # queue not readable: previous behaviour
            np = await self.now_playing()
            if np is None:
                await self._req("PATCH", "/queue", {"index": 0})
            elif np.video_id != track.video_id:
                await self._req("POST", "/next")
        await self.resume()

    async def _select(self, idx: int, video_id: str, tries: int = 3) -> None:
        """Jump to queue index idx and CHECK that the selected item is video_id. QA 19 Oct on a ~100-item real
        queue: the queue shifted by one between our read and the jump, so Pear kept playing the PREVIOUS request
        (10/10 plays in a row). On a mismatch, read the queue again and re-select the song."""
        await self._req("PATCH", "/queue", {"index": idx})
        for attempt in range(tries):
            await asyncio.sleep(0.4)
            q = await self.queue()
            cur = next((i for i, (_, sel) in enumerate(q) if sel), -1)
            if not q or (0 <= cur < len(q) and q[cur][0] == video_id):
                return
            if 0 <= idx < len(q) and q[idx][0] == video_id:
                continue        # the song is still at our index: "selected" only lags; jumping again froze playback
            cands = [i for i, (vid, _) in enumerate(q) if vid == video_id]
            if not cands:
                return
            later = [i for i in cands if i > cur]
            idx = later[0] if later else cands[-1]
            logs.info("music", "MUSIC_QUEUE_RESELECT", "queue shifted under the jump; selecting the song again",
                      attempt=attempt + 1)
            await self._req("PATCH", "/queue", {"index": idx})

    async def pause(self) -> None:
        await self._req("POST", "/pause")

    async def resume(self) -> None:
        await self._req("POST", "/play")

    async def next(self) -> None:
        async with self._exclusive():
            await self._next()

    async def _next(self) -> None:
        """Next DIFFERENT song: back-to-back copies of the current one (left by earlier inserts) are skipped by
        selecting the first other item; otherwise Pear's own next (it also handles autoplay at the queue end)."""
        q = await self.queue()
        cur = next((i for i, (_, sel) in enumerate(q) if sel), -1)
        if cur >= 0:
            other = next((i for i in range(cur + 1, len(q)) if q[i][0] != q[cur][0]), None)
            if other is not None and other > cur + 1:
                await self._req("PATCH", "/queue", {"index": other})
                return
        await self._req("POST", "/next")

    async def set_volume(self, percent: int) -> None:
        await self._req("POST", "/volume", {"volume": percent})

    async def now_playing(self) -> NowPlaying | None:
        r = await self._req("GET", "/song")
        if r.status_code == 204:
            return None
        d = _json(r)
        if not isinstance(d, dict):
            return None
        return NowPlaying(str(d.get("title") or ""), str(d.get("artist") or ""), str(d.get("videoId") or ""),
                          bool(d.get("isPaused", True)), _num(d.get("elapsedSeconds")), _num(d.get("songDuration")))


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


async def _false() -> bool:
    return False


# ------------------------------------------------------------------ Skill
class PlayArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=200, description="Song, artist or 'artist song' to play")


class VolumeArgs(BaseModel):
    percent: int = Field(..., ge=0, le=100, description="Music volume 0-100")


class MusicSkill(Skill):
    name = "music"
    description = "Play music in the user's YouTube Music player (Pear Desktop)"

    def __init__(self, player: MusicPlayer, max_volume: int = 70, verify_tries: int = 16) -> None:
        self.player = player
        self.verify_tries = verify_tries
        self._plays = 0
        self._trim_task = None
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
                   "MUSIC_NOT_PAIRED": "עוד לא חיברו אותי לנגן המוזיקה. צריך להריץ jarves music pair ולאשר.",
                   "MUSIC_TIMEOUT": "נגן המוזיקה לא עונה."}.get(e.code, "יש בעיה בנגן המוזיקה.")
            return ToolResult(False, msg, {"action": e.action}, e.code)

    @staticmethod
    def _same_track(np: NowPlaying, t: Track) -> bool:
        """Same videoId, or the same title: YouTube Music swaps a music video for its "song" counterpart (a
        different videoId) when it plays it - seen on real Pear 3.12, 18 Oct 2026. Titles are compared without bidi
        marks and without video/remaster decorations ("X (Official Video)" == "X"); a different song never matches."""
        if np.video_id and np.video_id == t.video_id:
            return True
        a, b = clean_title(np.title), clean_title(t.title)
        if a and a == b:
            return True
        a, b = base_title(np.title), base_title(t.title)
        return bool(a) and a == b

    def _spawn_trim(self) -> None:
        """Keep the player's queue small in the background (never blocks or fails a play)."""
        trim = getattr(self.player, "trim_history", None)
        if trim is None or (self._trim_task is not None and not self._trim_task.done()):
            return

        async def go() -> None:
            try:
                n = await trim()
                if n:
                    logs.debug("music", "QUEUE_TRIMMED", f"removed {n} played items from the player's queue")
            except Exception:
                logs.debug("music", "QUEUE_TRIM_FAILED", "could not trim the player's queue")
        self._trim_task = asyncio.get_running_loop().create_task(go())

    async def _verify_playing(self, t: Track, tries: int | None = None) -> NowPlaying | None:
        """Truthful status: report success only when the player itself says this track is playing. Polls up to
        verify_tries x 0.5 s (a fresh track can take several seconds to buffer) and returns at once when it is."""
        # Not usable as a progress signal: Pear's elapsedSeconds stays 0 while really playing and is refreshed only
        # on events (QA 19 Oct: 0 for ~9 s, then 9 right after a pause). Requiring it gave false "not confirmed".
        np = None
        for _ in range(tries or self.verify_tries):
            np = await self.player.now_playing()
            if np and self._same_track(np, t) and not np.paused:
                return np
            await asyncio.sleep(0.5)
        return None

    def tools(self) -> list[Tool]:
        p = self.player

        async def play(a: PlayArgs):
            async def go():
                found = await p.search(a.query)
                if not found:
                    return ToolResult(False, f"לא מצאתי את \"{a.query}\".", code="MUSIC_NOT_FOUND")
                # Some YouTube results cannot play (region / availability; real Pear, 19 Oct: "Get Lucky" top result,
                # Pear then skipped to an unrelated song). Try the next candidates instead of giving up (max 3).
                np, t, tried = None, found[0], []
                for cand in found[:3]:
                    if any(clean_title(cand.title) == clean_title(u.title) for u in tried):
                        continue                              # the same song again (another upload): not a new try
                    tried.append(cand)
                    t = cand
                    await p.play_track(cand)
                    np = await self._verify_playing(cand, tries=None if len(tried) == 1 else max(2, self.verify_tries * 3 // 4))
                    if np is not None:
                        break
                    logs.info("music", "MUSIC_CANDIDATE_FAILED", f"candidate {len(tried)} did not start; trying the next")
                if np is None:
                    return ToolResult(False, "ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.",
                                      {"track": asdict(t), "tried": len(tried)}, "MUSIC_NOT_CONFIRMED")
                self._plays += 1
                if self._plays % 6 == 1:                    # amortised: the first play, then every 6th
                    self._spawn_trim()
                return ToolResult(True, f"מנגן: {np.title} – {np.artist}", {"now_playing": asdict(np)})
            return await self._guard(go())

        def nothing(ok: bool) -> ToolResult:
            return ToolResult(ok, "שום דבר לא מתנגן כרגע." + ("" if ok else " אפשר לבקש ממני שיר."),
                              {"now_playing": None}, "ok" if ok else "MUSIC_NOTHING_PLAYING")

        def simple(op, ok_msg):
            """pause / stop / resume / next. Nothing loaded in the player -> say so instead of claiming an action
            (QA 19 Oct: "next" answered "השיר הבא." with nothing playing). next/resume are confirmed with the
            player itself, and next names the new song."""
            async def run(_):
                async def go():
                    before = await p.now_playing()
                    if before is None or not (before.title or before.video_id):
                        return nothing(op == "pause")
                    await getattr(p, op)()
                    if op == "pause":
                        # a pause pressed while a track is still loading can be ignored by the player (real Pear, 19 Oct:
                        # "השהיתי" was said while it kept playing). Verify with the player, press once more, else say so.
                        loop = asyncio.get_running_loop()
                        t0, pressed_again = loop.time(), False
                        limit = 4.0 if self.verify_tries >= 10 else 0.4 * max(2, self.verify_tries)
                        while True:
                            now = await p.now_playing()
                            if now is None or now.paused:
                                return ToolResult(True, ok_msg)
                            if loop.time() - t0 > limit:
                                break
                            if not pressed_again and loop.time() - t0 > 1.5:
                                pressed_again = True
                                await p.pause()
                            await asyncio.sleep(0.3)
                        return ToolResult(False, "ביקשתי מהנגן להשהות, אבל הוא עדיין מנגן.", code="MUSIC_NOT_CONFIRMED")
                    # real Pear 3.12: "next" shows up after 0.9-3.3 s and is occasionally dropped while a track is
                    # loading -> poll ~7 s and press it once more if nothing changed after ~4 s
                    loop = asyncio.get_running_loop()
                    t0, pressed_again = loop.time(), False
                    limit = 7.0 if self.verify_tries >= 10 else 0.4 * max(2, self.verify_tries)
                    while True:
                        np = await p.now_playing()
                        if np and not np.paused and (op == "resume" or np.video_id != before.video_id):
                            return ToolResult(True, f"{ok_msg} {np.title} – {np.artist}".strip(" –"),
                                              {"now_playing": asdict(np)})
                        if loop.time() - t0 > limit:
                            break
                        if op == "next" and not pressed_again and loop.time() - t0 > 4.0:
                            pressed_again = True
                            logs.info("music", "MUSIC_NEXT_RETRY", "next not reflected after 4 s; pressing again")
                            await p.next()
                        await asyncio.sleep(0.3)
                    what = "לעבור לשיר הבא" if op == "next" else "להמשיך לנגן"
                    return ToolResult(False, f"ביקשתי מהנגן {what}, אבל הוא לא מדווח שזה קרה.", code="MUSIC_NOT_CONFIRMED")
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
                 PlayArgs, permission="action", timeout_s=25),
            Tool("music_pause", "Pause music.", simple("pause", "השהיתי."), permission="action"),
            Tool("music_resume", "Resume music.", simple("resume", "ממשיך לנגן:"), permission="action"),
            Tool("music_next", "Skip to the next track.", simple("next", "השיר הבא:"), permission="action",
                 timeout_s=15),
            Tool("music_stop", "Stop music (pauses the player).", simple("pause", "עצרתי את המוזיקה."),
                 permission="action"),
            Tool("music_volume", "Set the music volume (0-100).", volume, VolumeArgs, permission="action"),
            Tool("music_status", "What is playing now.", status, permission="read"),
        ]

    async def health(self) -> tuple[str, str]:
        try:
            await self.player.now_playing()
            return "ok", f"{self.player.name} reachable and paired"
        except MusicError as e:
            if e.code == "MUSIC_NOT_PAIRED" and await getattr(self.player, "reachable", _false)():
                return "degraded", f"{self.player.name} running but not paired; {e.action}"
            return "unavailable", f"{e.message}; {e.action}"
