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
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, model_validator

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
    # optional (MusicSkill checks with getattr): get_volume() -> (slider 0-100, muted), toggle_mute(), trim_history()


# ------------------------------------------------------------------ volume scale (Pear Desktop 3.12)
# POST /volume moves Pear's SLIDER (0-100); GET /volume reports the player's effective level on a lower curve.
# Measured on the real player (paused, 19 Oct 2026; tools/suite_music.py notes): slider -> reported state.
# POST 0 also mutes; any other POST unmutes.
_VOLUME_CURVE = ((0, 0), (5, 1), (10, 2), (20, 5), (30, 8), (40, 13), (50, 20), (60, 29), (70, 40), (80, 55),
                 (90, 74), (100, 100))


def slider_from_state(state: float) -> int:
    """Pear's reported volume state -> the slider position the user sees (and that POST /volume takes)."""
    s = max(0.0, min(100.0, _num(state)))
    for (x0, y0), (x1, y1) in zip(_VOLUME_CURVE, _VOLUME_CURVE[1:]):
        if s <= y1:
            return round(x0 + (x1 - x0) * (s - y0) / (y1 - y0))
    return 100


def _volume_tolerance(slider: int) -> int:
    """How far a read-back slider may be from the one set and still be the same: Pear reports whole state units,
    which are ~5 slider points apart near the bottom of the curve (slider 2 and 3 both read state 0)."""
    return 5 if slider <= 10 else 4 if slider <= 30 else 2


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

    def walk(o, under: str = "") -> None:
        if isinstance(o, dict):
            card = o.get("musicCardShelfRenderer")
            if card:   # "top result"
                ep = (card.get("title", {}).get("runs") or [{}])[0].get("navigationEndpoint", {})
                add(ep.get("watchEndpoint", {}).get("videoId"), _text(card.get("title")), _text(card.get("subtitle")))
                # the songs listed INSIDE an artist card omit the artist ("שיר • 3:24"): they inherit the card's name
                # (real Pear 19 Oct 2026, "נועה קירל": the card is Hebrew, every row only says "Noa Kirel" or nothing)
                walk(card.get("contents"), _text(card.get("title")))
                return
            item = o.get("musicResponsiveListItemRenderer")
            if item:
                # songs/videos carry the id in playlistItemData, or (real Pear 19 Oct 2026, "ABBA Dancing Queen":
                # every row) only in the row's watch endpoint / play-button overlay; albums/artists have neither
                vid = (item.get("playlistItemData") or {}).get("videoId") or \
                    ((item.get("navigationEndpoint") or {}).get("watchEndpoint") or {}).get("videoId") or \
                    (((((item.get("overlay") or {}).get("musicItemThumbnailOverlayRenderer") or {}).get("content") or {})
                      .get("musicPlayButtonRenderer") or {}).get("playNavigationEndpoint", {}).get("watchEndpoint") or {}).get("videoId")
                cols = item.get("flexColumns") or []
                txt = [_text(c.get("musicResponsiveListItemFlexColumnRenderer", {}).get("text")) for c in cols]
                sub = " ".join(txt[1:2])
                add(vid, txt[0] if txt else "", f"{sub} • {under}" if under else sub)
            for v in o.values():
                walk(v, under)
        elif isinstance(o, list):
            for v in o:
                walk(v, under)
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
    -> a random 2.7K-view user video, then generic hits). True when at least one word of the query appears
    somewhere in what YouTube shows. Page level only: play candidates are checked one by one (track_is_relevant)."""
    words = _query_words(query)
    if not words:
        return True
    shown = _display_text(resp)
    return any(w in shown for w in words)


# words that say nothing about WHICH song (a candidate matching only these is not relevant)
_STOPWORDS = frozenset("של את לי עם גם the by of a an and to for me some music song songs play "
                       "שיר שירים מוזיקה תנגן נגן".split())
_HE_PREFIX = "הובלמשכ"


def _query_words(query: str) -> list[str]:
    return [w for w in re.findall(r"\w+", clean_title(query)) if len(w) >= 2 and w not in _STOPWORDS]


def _word_in(word: str, tokens: set[str]) -> bool:
    """A query word among a candidate's words (whole words: "it" must not match "with"). Tolerated: a one-letter
    Hebrew prefix on either side ("רגוע" ~ "הרגוע", "הביטלס" ~ "ביטלס") and a Latin plural/possessive tail."""
    if word in tokens:
        return True
    if "֐" <= word[0] <= "׿":
        return any(len(t) >= 3 and ((t[0] in _HE_PREFIX and t[1:] == word) or
                                    (word[0] in _HE_PREFIX and word[1:] == t)) for t in tokens)
    return len(word) >= 4 and any(t.startswith(word) and len(t) - len(word) <= 2 for t in tokens)


def track_is_relevant(t: Track, query: str) -> bool:
    """Issue #16 (P2): a PLAYABLE candidate must itself mention the query (its title or artist/subtitle line), not
    just some other part of the result page (a random top video must not play because the query word appears in
    another card further down). Queries made only of filler words accept anything."""
    words = _query_words(query)
    if not words:
        return True
    tokens = set(re.findall(r"\w+", clean_title(f"{t.title} {t.subtitle}")))
    return any(_word_in(w, tokens) for w in words)


_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|•|·|/|\+|\bfeat\.?|\bft\.?|\band\b|\bx\b|\sו-)\s*")
_KIND_WORDS = {"song", "video", "single", "ep", "album", "שיר", "סרטון", "סינגל", "אלבום", "topic", "vevo"}


def _artist_names(s: str) -> list[str]:
    return [n.replace(" ", "") for n in _ARTIST_SPLIT.split(clean_title(s))
            if len(n) >= 2 and not any(c.isdigit() for c in n) and n not in _KIND_WORDS]


def _script(s: str) -> str:
    """'he', 'latin' or 'mixed' (Pear's Hebrew UI writes "Noa Kirel ו-Ilan Peled": names Latin, the "and" Hebrew)."""
    he, latin = any("֐" <= c <= "׿" for c in s), any("a" <= c <= "z" for c in s.casefold())
    return "mixed" if he and latin else "he" if he else "latin"


def artists_match(player_artist: str, searched: Track) -> bool | None:
    """Do the artist the player reports and the searched candidate (its subtitle line / title) agree?
    None = cannot tell (an empty side, or Hebrew vs Latin spelling of what may be the same artist)."""
    names = _artist_names(player_artist)
    segs = _artist_names(searched.subtitle)          # the subtitle's name parts ("שיר", "4:09", "3M views" left out)
    script = _script(" ".join(names))
    if not names or not segs or script == "mixed" or script != _script(" ".join(segs)):
        return None
    flat = clean_title(f"{searched.subtitle} {searched.title}").replace(" ", "")
    return any(n in flat for n in names) or any(s in n or n in s for s in segs for n in names if len(s) >= 3)


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
        tracks = parse_search(d, limit=10)
        relevant = [t for t in tracks if track_is_relevant(t, query)][:5]
        if tracks and not relevant:
            logs.info("music", "MUSIC_SEARCH_IRRELEVANT", "no playable result mentions the query; treated as not found",
                      results=len(tracks), page_mentions_query=search_is_relevant(d, query))
        return relevant

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

    async def get_volume(self) -> tuple[int, bool]:
        """(slider position 0-100 as the user sees it, muted). GET /volume reports the effective level on a lower
        curve (state 40 = slider 70), so it is converted back; see _VOLUME_CURVE."""
        d = _json(await self._req("GET", "/volume"))
        if not isinstance(d, dict):
            raise MusicError("MUSIC_PLAYER_ERROR", "Pear Desktop sent an unreadable volume", "update or restart Pear Desktop")
        return slider_from_state(d.get("state")), bool(d.get("isMuted"))

    async def toggle_mute(self) -> None:
        await self._req("POST", "/toggle-mute")

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
        self.muted = False
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
        self.muted = percent == 0             # like Pear: POST 0 mutes, any other value unmutes

    async def get_volume(self) -> tuple[int, bool]:
        self._chk("get_volume")
        return self.volume, self.muted

    async def toggle_mute(self) -> None:
        self._chk("toggle_mute")
        self.muted = not self.muted

    async def now_playing(self) -> NowPlaying | None:
        self._chk("now_playing")
        return self.np


async def _false() -> bool:
    return False


# ------------------------------------------------------------------ Skill
class PlayArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=200, description="Song, artist or 'artist song' to play")


class VolumeArgs(BaseModel):
    percent: int | None = Field(None, ge=0, le=100,
                                description="Absolute music volume 0-100 (only when the user names a number)")
    change: Literal["up", "down"] | None = Field(
        None, description="Relative change: 'up' for louder (תגביר/יותר חזק), 'down' for quieter (תנמיך/יותר חלש)")
    step: int = Field(10, ge=1, le=50, description="Points per relative change (default 10; 'a lot' = 20)")

    @model_validator(mode="after")
    def _one(self):
        if (self.percent is None) == (self.change is None):
            raise ValueError("give exactly one of percent or change")
        return self


PLAY_TIMEOUT_S = 25.0       # the registry's hard limit for music_play
PLAY_BUDGET_S = 21.0        # ONE end-to-end deadline for search + candidates + confirm, inside the tool limit
MIN_CANDIDATE_S = 4.0       # do not start another candidate with less time left than this


class MusicSkill(Skill):
    name = "music"
    description = "Play music in the user's YouTube Music player (Pear Desktop)"

    def __init__(self, player: MusicPlayer, max_volume: int = 70, verify_tries: int = 16, *,
                 trim_queue: bool = True, volume_step: int = 10, play_budget_s: float = PLAY_BUDGET_S) -> None:
        self.player = player
        self.verify_tries = verify_tries
        self._plays = 0
        self._trim_task: asyncio.Task | None = None
        self._moved_on = ""          # set by _verify_playing: the song the player skipped to instead
        # Issue #16: trimming removes PLAYED items from Pear's queue (its visible history). Config music.trim_queue.
        self.trim_queue = trim_queue
        self.volume_step = volume_step
        self.play_budget_s = min(play_budget_s, PLAY_TIMEOUT_S - 2)
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
        """Same videoId; or (narrow exception) the same song under another videoId: YouTube Music swaps a music
        video for its "song" counterpart when it plays it (real Pear 3.12, 18 Oct 2026). Titles are compared without
        bidi marks and video/remaster decorations ("X (Official Video)" == "X"), and the ARTIST must agree too
        when both sides name one in the same script (Issue #16: same title by another artist is another song)."""
        if np.video_id and np.video_id == t.video_id:
            return True
        a, b = clean_title(np.title), clean_title(t.title)
        same_title = bool(a) and a == b
        if not same_title:
            a, b = base_title(np.title), base_title(t.title)
            same_title = bool(a) and a == b
        return same_title and artists_match(np.artist, t) is not False

    def _spawn_trim(self) -> None:
        """Keep the player's queue small in the background (never blocks or fails a play). Off with
        music.trim_queue = false (then Pear's played history is never touched)."""
        trim = getattr(self.player, "trim_history", None)
        if not self.trim_queue or trim is None or (self._trim_task is not None and not self._trim_task.done()):
            return

        async def go() -> None:
            try:
                n = await trim()
                if n:
                    logs.debug("music", "QUEUE_TRIMMED", f"removed {n} played items from the player's queue")
            except asyncio.CancelledError:
                logs.debug("music", "QUEUE_TRIM_CANCELLED", "queue trim cancelled (shutdown)")
                raise
            except Exception:
                logs.debug("music", "QUEUE_TRIM_FAILED", "could not trim the player's queue")
        self._trim_task = asyncio.get_running_loop().create_task(go())

    async def close(self) -> None:
        """Core shutdown: stop a background trim (it gives way between single deletes) before the client closes."""
        t, self._trim_task = self._trim_task, None
        if t is not None and not t.done():
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        if hasattr(self.player, "close"):
            await self.player.close()

    async def _verify_playing(self, t: Track, tries: int | None = None,
                              deadline: float | None = None, known: set[str] | None = None) -> NowPlaying | None:
        """Truthful status: success only when the player itself says THIS track (id, or title + artist) is playing.
        Polls up to tries x 0.5 s (a fresh track can take seconds to buffer), never past `deadline` (loop time).
        Issue #16 P1: Pear has NO live progress signal. Its elapsedSeconds (REST, and the position in the
        /api/v1/ws events) is refreshed only on state events: 19 Oct 2026, a song really played 27 s -> 36 s while
        both read 27 the whole time, so a stalled video and a playing one look the same, and requiring progress gave
        0/10 false "not confirmed" (QA 19 Oct). What is confirmed is therefore "the player reports this song
        playing"; results say so (data playback = "player_reports_playing") and never claim it is audible.
        known: songs the player showed before (the previous song, earlier failed tries). A song NOT in it playing on
        two polls in a row means the player skipped an unplayable result (real Pear 19 Oct: "Daft Punk Get Lucky"
        -> "Love Again"): give up on this candidate at once (self._moved_on = that song) instead of polling 8 s."""
        loop = asyncio.get_running_loop()
        self._moved_on, other = "", ""
        for _ in range(tries or self.verify_tries):
            try:
                np = await (self.player.now_playing() if deadline is None else
                            asyncio.wait_for(self.player.now_playing(), max(0.3, deadline - loop.time())))
            except TimeoutError:
                np = None                                     # the deadline ran out mid-poll
            except MusicError as e:
                # one slow answer while Pear switches songs (S11, real Pear 19 Oct: MUSIC_TIMEOUT mid-confirm) is not
                # a verdict; keep polling inside the deadline. Offline / unpaired still fail at once.
                if e.code != "MUSIC_TIMEOUT":
                    raise
                np = None
            if np and self._same_track(np, t) and not np.paused:
                return np
            if known is not None and np and np.video_id and not np.paused and np.video_id not in known:
                if other == np.video_id:
                    self._moved_on = other
                    logs.info("music", "MUSIC_PLAYER_SKIPPED", "the player moved on to another song; result not playable")
                    return None
                other = np.video_id
            else:
                other = ""
            if deadline is not None and loop.time() + 0.5 > deadline:
                break
            await asyncio.sleep(0.5)
        return None

    def tools(self) -> list[Tool]:
        p = self.player

        async def play(a: PlayArgs):
            async def go():
                # Issue #16 P2: ONE end-to-end deadline (search, every candidate's queue work and confirm polling)
                # that ends before the registry's tool timeout, so the answer is always this function's own truthful
                # result, never a TOOL_TIMEOUT after the queue was already changed.
                loop = asyncio.get_running_loop()
                deadline = loop.time() + self.play_budget_s
                left = lambda: deadline - loop.time()           # noqa: E731
                try:
                    found = await asyncio.wait_for(p.search(a.query), max(0.5, left() - MIN_CANDIDATE_S))
                except TimeoutError as e:
                    raise MusicError("MUSIC_TIMEOUT", "Pear Desktop search did not answer in time",
                                     "check that the app is not frozen") from e
                if not found:
                    return ToolResult(False, f"לא מצאתי את \"{a.query}\".", code="MUSIC_NOT_FOUND")
                # Some YouTube results cannot play (region / availability; real Pear, 19 Oct: "Get Lucky" top result,
                # Pear then skipped to an unrelated song). Try the next candidates instead of giving up (max 3).
                np, t, tried, out_of_time = None, found[0], [], False
                seen: set[str] = set()                        # songs the player showed that are NOT a skip-on
                with contextlib.suppress(MusicError):
                    was = await asyncio.wait_for(p.now_playing(), max(0.3, min(3.0, left() - MIN_CANDIDATE_S)))
                    if was is not None and was.video_id:
                        seen.add(was.video_id)
                for cand in found:
                    if len(tried) >= 3:
                        break
                    if any(clean_title(cand.title) == clean_title(u.title) for u in tried):
                        continue                              # the same song again (another upload): not a new try
                    if tried and left() < MIN_CANDIDATE_S:
                        out_of_time = True
                        break
                    tried.append(cand)
                    t = cand
                    try:
                        await asyncio.wait_for(p.play_track(cand), max(0.5, left() - 1.0))
                    except TimeoutError:
                        out_of_time = True
                        break
                    # every try gets the full window (a slow Pear needed 4-9 s per switch in the 19 Oct campaign), only
                    # the deadline bounds it; a result the player skips fails fast (known songs, see _verify_playing)
                    np = await self._verify_playing(cand, deadline=deadline, known=seen)
                    if np is not None:
                        break
                    if self._moved_on:
                        seen.add(self._moved_on)
                    logs.info("music", "MUSIC_CANDIDATE_FAILED", f"candidate {len(tried)} did not start; trying the next")
                if np is None and tried:
                    # a slow player may have started an EARLIER try after we moved on (S11, real Pear 19 Oct: "נועה
                    # קירל" ended on the 2nd candidate, reported "not confirmed"): one last look before saying no
                    with contextlib.suppress(MusicError, TimeoutError):
                        last = await asyncio.wait_for(p.now_playing(), max(0.3, min(2.0, left())))
                        hit = next((u for u in tried if last and not last.paused and self._same_track(last, u)), None)
                        if hit is not None:
                            np, t = last, hit
                if np is None:
                    if out_of_time or left() <= 0.5:
                        logs.warn("music", "MUSIC_PLAY_DEADLINE", "the player was too slow; stopped trying candidates",
                                  tried=len(tried), budget_s=self.play_budget_s)
                        msg = (f"ניסיתי {len(tried)} גרסאות של השיר, ואף אחת לא התחילה לנגן בזמן." if len(tried) > 1
                               else "הנגן איטי מדי כרגע: ביקשתי לנגן, אבל לא קיבלתי ממנו אישור בזמן.")
                        return ToolResult(False, msg,
                                          {"track": asdict(t), "tried": len(tried), "deadline_s": self.play_budget_s,
                                           "note": "the player may still start this song late"}, "MUSIC_NOT_CONFIRMED")
                    return ToolResult(False, "ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.",
                                      {"track": asdict(t), "tried": len(tried)}, "MUSIC_NOT_CONFIRMED")
                self._plays += 1
                if self._plays % 6 == 1:                    # amortised: the first play, then every 6th
                    self._spawn_trim()
                return ToolResult(True, f"מנגן: {np.title} – {np.artist}",
                                  {"now_playing": asdict(np), "playback": "player_reports_playing",
                                   "note": "the player reports this song playing; JARVES cannot hear the speakers"})
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
                        # an ignored pause is also the symptom of a stalled video (QA 19 Oct): say what helps
                        return ToolResult(False, "ביקשתי מהנגן להשהות, אבל הוא עדיין מדווח שהוא מנגן. אם זה נמשך, "
                                          "כדאי לסגור ולפתוח מחדש את Pear Desktop.",
                                          {"action": "restart Pear Desktop if the player stays stuck"},
                                          "MUSIC_NOT_CONFIRMED")
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
                        # real Pear 19 Oct (S07): a resume right after a pause was applied only ~9 s later; POST /play
                        # is not a toggle, so pressing it once more is safe
                        if op == "resume" and not pressed_again and loop.time() - t0 > 2.5:
                            pressed_again = True
                            logs.info("music", "MUSIC_RESUME_RETRY", "resume not reflected after 2.5 s; pressing again")
                            await p.resume()
                        await asyncio.sleep(0.3)
                    what = "לעבור לשיר הבא" if op == "next" else "להמשיך לנגן"
                    return ToolResult(False, f"ביקשתי מהנגן {what}, אבל הוא לא מדווח שזה קרה.", code="MUSIC_NOT_CONFIRMED")
                return await self._guard(go())
            return run

        async def volume(a: VolumeArgs):
            """Real session 19 Oct: "תגביר" jumped straight to the 70 cap and "what volume is it?" was answered from
            memory. Relative steps from the REAL current level, read back after the change; the cap is reported."""
            async def go():
                cap = self.max_volume
                cur = await self._read_volume()
                before = cur[0] if cur else None
                if a.percent is not None:
                    want = a.percent
                elif before is None:
                    return ToolResult(False, "אני לא יודע מה העוצמה כרגע, אז אי אפשר לשנות אותה ביחס אליה. "
                                      "אפשר לבקש מספר מ-0 עד 100.", {"max_volume": cap}, "MUSIC_VOLUME_UNKNOWN")
                else:
                    step = a.step if "step" in a.model_fields_set else self.volume_step
                    want = max(0, min(100, before + (step if a.change == "up" else -step)))
                if before is not None and want > before and before >= cap:     # louder than the cap: never
                    return ToolResult(True, f"העוצמה כבר {before} מתוך 100, וזו התקרה שהוגדרה ({cap}); לא הגברתי.",
                                      {"volume": before, "previous": before, "max_volume": cap, "changed": False,
                                       "at_limit": True, "muted": cur[1]})
                v = min(want, cap)
                await p.set_volume(v)
                after = await self._read_volume(expect=v)
                real = after[0] if after else v
                if after and abs(after[0] - v) <= _volume_tolerance(v):
                    real = v                                  # the reported level is coarse: consistent = what we set
                muted = after[1] if after else (v == 0)
                capped = want > cap
                if a.change and before is not None:
                    verb = "הגברתי" if real > before else "הנמכתי" if real < before else "העוצמה נשארה"
                    msg = f"{verb} מ-{before} ל-{real} מתוך 100." if real != before else f"{verb} {real} מתוך 100."
                else:
                    msg = f"העוצמה עכשיו {real} מתוך 100."
                if capped:
                    msg += f" (התקרה שהוגדרה היא {cap})"
                if cur and cur[1] and not muted:
                    msg += " ההשתקה בוטלה."
                return ToolResult(True, msg, {"volume": real, "previous": before, "max_volume": cap, "changed": True,
                                              "capped": capped, "muted": muted})
            return await self._guard(go())

        def mute_tool(want_muted: bool):
            """Mute is NOT pause (real session 19 Oct: "תעשה מיוט" paused the song). Pear's toggle-mute keeps the
            song playing and keeps the slider; verified with GET /volume."""
            async def run(_):
                async def go():
                    if getattr(p, "toggle_mute", None) is None or getattr(p, "get_volume", None) is None:
                        return ToolResult(False, "הנגן הזה לא תומך בהשתקה.", code="MUSIC_UNSUPPORTED")
                    vol, muted = await p.get_volume()
                    if muted == want_muted:
                        msg = "המוזיקה כבר מושתקת." if muted else f"המוזיקה לא מושתקת; העוצמה {vol} מתוך 100."
                        return ToolResult(True, msg, {"muted": muted, "volume": vol, "changed": False})
                    await p.toggle_mute()
                    after = await self._read_volume(muted=want_muted)
                    if after is None or after[1] != want_muted:
                        what = "להשתיק" if want_muted else "לבטל את ההשתקה"
                        return ToolResult(False, f"ביקשתי מהנגן {what}, אבל הוא לא מדווח שזה קרה.",
                                          {"muted": after[1] if after else None}, "MUSIC_NOT_CONFIRMED")
                    if want_muted:
                        msg = "השתקתי את המוזיקה. השיר ממשיך להתנגן בלי קול."
                    else:
                        msg = f"החזרתי את הקול. העוצמה {after[0]} מתוך 100." + (
                            " היא על 0, אז צריך גם להגביר." if after[0] == 0 else "")
                    return ToolResult(True, msg, {"muted": want_muted, "volume": after[0], "changed": True,
                                                  "still_playing": True})
                return await self._guard(go())
            return run

        async def status(_):
            async def go():
                np = await p.now_playing()
                vol = None
                with contextlib.suppress(MusicError):
                    vol = await self._read_volume()
                extra = {"volume": vol[0], "muted": vol[1], "max_volume": self.max_volume} if vol else {}
                level = (f" עוצמה {vol[0]} מתוך 100" + (", מושתק" if vol[1] else "") + ".") if vol else ""
                if np is None or not np.title:
                    return ToolResult(True, "שום דבר לא מתנגן כרגע." + level, {"now_playing": None, **extra})
                state = "מושהה" if np.paused else "מתנגן"
                return ToolResult(True, f"{state}: {np.title} – {np.artist}.{level}", {"now_playing": asdict(np), **extra})
            return await self._guard(go())

        return [
            Tool("music_play", "Search YouTube Music and start playing the best match. query: song and/or artist in "
                 "its usual spelling (e.g. 'Daft Punk Get Lucky', 'שלום חנוך'); for a mood or genre use concrete genre "
                 "words ('calm piano', 'lofi'). The result says what the PLAYER reports playing.", play,
                 PlayArgs, permission="action", timeout_s=PLAY_TIMEOUT_S),
            Tool("music_pause", "Pause the music: the song stops where it is (music_resume continues it). NOT for "
                 "'mute'/'השתק'/'מיוט': use music_mute.", simple("pause", "השהיתי."), permission="action"),
            Tool("music_resume", "Resume paused music.", simple("resume", "ממשיך לנגן:"), permission="action"),
            Tool("music_next", "Skip to the next track.", simple("next", "השיר הבא:"), permission="action",
                 timeout_s=15),
            Tool("music_stop", "Stop the music (pauses the player).", simple("pause", "עצרתי את המוזיקה."),
                 permission="action"),
            Tool("music_mute", "Mute the music WITHOUT pausing it: the song keeps playing silently. Use for 'mute', "
                 "'השתק', 'תעשה מיוט', 'בלי קול'.", mute_tool(True), permission="action"),
            Tool("music_unmute", "Unmute the music (bring its sound back). Use for 'unmute', 'בטל השתקה', "
                 "'תחזיר את הקול'.", mute_tool(False), permission="action"),
            Tool("music_volume", "Change the music volume (0-100, Pear's slider). Louder/'תגביר': change='up'; "
                 "quieter/'תנמיך': change='down' (10 points a step; never jump to the maximum). Use percent only "
                 "when the user names a number. The result reports the real level before and after and the allowed "
                 "maximum (max_volume).", volume, VolumeArgs, permission="action"),
            Tool("music_status", "What is playing now, whether it is paused, and the current volume (0-100) and "
                 "mute state. Use it to answer 'what volume is it?'.", status, permission="read"),
        ]

    async def _read_volume(self, expect: int | None = None, muted: bool | None = None) -> tuple[int, bool] | None:
        """(slider 0-100, muted) from the player, or None when it cannot report volume. With expect/muted: wait
        up to ~2 s for Pear to apply a change (it applies volume asynchronously) and return the last reading."""
        get = getattr(self.player, "get_volume", None)
        if get is None:
            return None
        v = await get()
        for _ in range(10):
            if (expect is None or abs(v[0] - expect) <= _volume_tolerance(expect)) and (muted is None or v[1] == muted):
                break
            await asyncio.sleep(0.2)
            v = await get()
        return v

    async def health(self) -> tuple[str, str]:
        try:
            await self.player.now_playing()
            return "ok", f"{self.player.name} reachable and paired"
        except MusicError as e:
            if e.code == "MUSIC_NOT_PAIRED" and await getattr(self.player, "reachable", _false)():
                return "degraded", f"{self.player.name} running but not paired; {e.action}"
            return "unavailable", f"{e.message}; {e.action}"
