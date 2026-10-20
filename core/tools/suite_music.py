"""PRE-USE MUSIC TEST SUITE - numbered scenarios against the REAL Pear Desktop (YouTube Music), SILENT.

Safety first (the user may be asleep):
  * Pear's volume is set to 0 before anything runs, a watchdog keeps it at <= 3 the whole time, and the original
    volume is restored in a `finally` (even on a crash / Ctrl+C), with Pear left PAUSED.
  * No real microphone or speaker: voice scenarios inject synthesised Hebrew speech into a virtual mic and measure
    a virtual speaker (approach copied from tools/silent_live.py, not imported).
  * The user's state dir (%LOCALAPPDATA%\\jarves) is only READ: pear_client_id is copied to a temp dir (with the
    pairing marker honoured) and the API key is loaded into the environment, never printed. pair() is never called
    for an unpaired id (no Pear dialog); the paired copy only re-uses Pear's existing approval.
  * Voice scenarios use their own Core link port (8792) and a temp JARVES home.

Groups: skill (real Pear via MusicSkill), errors, robust (mock transports), voice (real GPT-Live; costs money),
soak (long, real Pear).  Results are merged into a store, so a group can be re-run alone; the report
docs/reviews/PRE_USE_TEST_REPORT_MUSIC.md is regenerated from the store after every run.

    uv --directory core run --no-sync python tools/suite_music.py [--groups skill,errors,robust,voice,soak]
        [--only S01,S05] [--soak-min 15] [--voice-max-min 8] [--restore-volume 8] [--fresh]
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import math
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
import tomllib

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np

from jarves.skills import SkillRegistry
from jarves.skills.music import FakePlayer, MusicSkill, NowPlaying, PearDesktopPlayer, Track

REAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/state"))) / "jarves"
REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "docs" / "reviews" / "PRE_USE_TEST_REPORT_MUSIC.md"
WORK = Path(tempfile.gettempdir()) / "jarves-suite-music"
STORE = WORK / "results.json"
PEAR = "http://127.0.0.1:26538"
SAFE_MAX_VOL = 3
LINK_PORT = 8792


def he(s: str) -> bool:
    return any("֐" <= c <= "׿" for c in s or "")


def norm(s: str) -> str:
    return " ".join((s or "").casefold().split())


# ======================================================================== transports
class LogTransport(httpx.AsyncBaseTransport):
    """Real HTTP, but every request is recorded (method, path, status) - e.g. to prove no /auth was sent."""

    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.log: deque = deque(maxlen=4000)
        self.count = 0

    async def handle_async_request(self, req: httpx.Request) -> httpx.Response:
        self.count += 1
        try:
            r = await self.inner.handle_async_request(req)
            self.log.append((time.time(), req.method, req.url.path, r.status_code))
            return r
        except Exception as e:
            self.log.append((time.time(), req.method, req.url.path, type(e).__name__))
            raise

    async def aclose(self) -> None:
        await self.inner.aclose()


def item(vid: str | None, title: str, artist: str = "") -> dict:
    d = {"flexColumns": [{"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": title}]}}},
                         {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": artist}]}}}]}
    if vid:
        d["playlistItemData"] = {"videoId": vid}
    return {"musicResponsiveListItemRenderer": d}


def search_resp(*tracks: tuple[str | None, str, str]) -> dict:
    return {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {"sectionListRenderer": {
        "contents": [{"musicShelfRenderer": {"contents": [item(*t) for t in tracks]}}]}}}}]}}}


class MockPear(httpx.AsyncBaseTransport):
    """In-memory Pear Desktop API (only the endpoints JARVES uses) with fault injection per path."""

    def __init__(self, catalog: list[tuple[str, str, str]] | None = None) -> None:
        self.catalog = catalog or [("v1", "Song One", "Artist A"), ("v2", "Song Two", "Artist B")]
        self.info = {v: (t, a) for v, t, a in self.catalog}
        self.queue: list[str] = []
        self.cur = -1
        self.paused = True
        self.counterpart: dict[str, tuple[str, str]] = {}      # searched video id -> (played id, played title)
        self.faults: dict[str, object] = {}                    # "METHOD path" -> callable(req) / Response / Exception
        self.log: list[tuple[str, str]] = []
        self.token = "tok1"
        self.search_override: object = None

    def _resp(self, code: int, body=None) -> httpx.Response:
        if body is None:
            return httpx.Response(code)
        if isinstance(body, (bytes, str)):
            return httpx.Response(code, content=body)
        return httpx.Response(code, json=body)

    async def handle_async_request(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        self.log.append((m, path))
        key = f"{m} {path}"
        f = self.faults.get(key)
        if f is not None:
            if isinstance(f, BaseException):
                raise f
            if callable(f):
                r = f(req)
                if r is not None:
                    return r
            elif isinstance(f, httpx.Response):
                return f
        if path.startswith("/auth/"):
            return self._resp(200, {"accessToken": self.token})
        if req.headers.get("authorization") != f"Bearer {self.token}":
            return self._resp(401, {"error": "unauthorized"})
        body = json.loads(req.content) if req.content else {}
        if key == "POST /api/v1/search":
            if self.search_override is not None:
                return self.search_override if isinstance(self.search_override, httpx.Response) else \
                    self._resp(200, self.search_override)
            q = norm(body.get("query", ""))
            hits = [c for c in self.catalog if any(w in norm(c[1] + " " + c[2]) for w in q.split())]
            return self._resp(200, search_resp(*hits))
        if key == "POST /api/v1/queue":
            self.queue.insert(self.cur + 1, body["videoId"])
            return self._resp(204)
        if key == "GET /api/v1/queue":
            return self._resp(200, {"items": [{"playlistPanelVideoRenderer": {"videoId": v, "selected": i == self.cur}}
                                              for i, v in enumerate(self.queue)]})
        if key == "PATCH /api/v1/queue":
            self.cur = int(body["index"])
            self.paused = False
            return self._resp(204)
        if key == "POST /api/v1/play":
            self.paused = self.cur < 0
            return self._resp(204)
        if key == "POST /api/v1/pause":
            self.paused = True
            return self._resp(204)
        if key == "POST /api/v1/next":
            if self.cur + 1 < len(self.queue):
                self.cur += 1
            return self._resp(204)
        if key == "POST /api/v1/volume":
            return self._resp(204)
        if key == "GET /api/v1/song":
            if self.cur < 0:
                return self._resp(204)
            vid = self.queue[self.cur]
            title, artist = self.info.get(vid, (vid, ""))
            if vid in self.counterpart:
                vid, title = self.counterpart[vid]
            return self._resp(200, {"title": title, "artist": artist, "videoId": vid, "isPaused": self.paused,
                                    "elapsedSeconds": 1, "songDuration": 200})
        return self._resp(404)


def mock_player(mp: MockPear, tmp: Path, paired: bool = True, timeout_s: float = 2.0) -> PearDesktopPlayer:
    idf = tmp / f"id-{secrets.token_hex(4)}"
    idf.write_text("jarves-mock", encoding="utf-8")
    if paired:
        idf.with_name(idf.name + ".paired").write_text("mock", encoding="utf-8")
    return PearDesktopPlayer("http://pear.mock", idf, timeout_s, transport=mp)


def reg_for(player, max_volume: int = 70, verify_tries: int = 16) -> tuple[SkillRegistry, MusicSkill]:
    reg = SkillRegistry()
    sk = MusicSkill(player, max_volume, verify_tries)
    reg.register(sk)
    return reg, sk


# ======================================================================== safety
class Safety:
    """Volume 0 before, watchdog during (never above SAFE_MAX_VOL), restore + pause after."""

    def __init__(self, restore_volume: int | None) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="jarves-suite-music-"))
        self.id_file = self.dir / "pear_client_id"
        shutil.copy(REAL / "pear_client_id", self.id_file)     # read-only use of the user's id
        if (REAL / "pear_client_id.paired").exists():
            self.id_file.with_name(self.id_file.name + ".paired").write_text("copy for the test suite\n", encoding="utf-8")
        self.admin = PearDesktopPlayer(PEAR, self.id_file, 5.0)
        self.restore_volume = restore_volume
        self.original: dict | None = None
        self.violations: list[tuple[float, int]] = []
        self.max_seen = 0
        self._task: asyncio.Task | None = None
        self.final: dict = {}

    def paired_copy(self) -> Path:
        d = Path(tempfile.mkdtemp(prefix="pear-id-", dir=self.dir))
        f = d / "pear_client_id"
        shutil.copy(self.id_file, f)
        f.with_name(f.name + ".paired").write_text("copy\n", encoding="utf-8")
        return f

    async def volume(self) -> dict:
        return (await self.admin._req("GET", "/volume")).json()

    async def set_volume(self, v: int, wait: float = 4.0) -> dict:
        """POST sets Pear's SLIDER position; GET reports the player's effective level on another (lower, ~quadratic)
        scale, e.g. POST 8 reads back as 1 (measured 19 Oct 2026). So only 0 can be verified exactly."""
        await self.admin._req("POST", "/volume", {"volume": v})
        t = time.time()
        st = await self.volume()
        while time.time() - t < (wait if v == 0 else 1.5):           # Pear applies it asynchronously
            if v == 0 and st.get("state") == 0:
                break
            await asyncio.sleep(0.2)
            st = await self.volume()
        return st

    async def start(self) -> None:
        self.original = await self.volume()
        if self.restore_volume is None:
            self.restore_volume = int(self.original.get("state", 0))
        st = await self.set_volume(0)
        if st.get("state") != 0:
            raise SystemExit(f"SAFETY: could not set Pear volume to 0 (now {st}); aborting")
        print(f"[safety] Pear volume was {self.original}; now {st}; will restore to {self.restore_volume} and pause")
        self._task = asyncio.create_task(self._watch())

    async def _watch(self) -> None:
        while True:
            try:
                st = await self.volume()
                v = int(st.get("state", 0))
                self.max_seen = max(self.max_seen, v)
                if v > SAFE_MAX_VOL:
                    self.violations.append((time.time(), v))
                    print(f"[safety] volume {v} > {SAFE_MAX_VOL}: forcing 0")
                    await self.admin._req("POST", "/volume", {"volume": 0})
            except Exception as e:                      # never let the watchdog die silently
                print(f"[safety] watchdog read failed: {type(e).__name__}")
            await asyncio.sleep(0.5)

    async def song(self) -> dict | None:
        r = await self.admin._req("GET", "/song")
        return r.json() if r.status_code == 200 and r.content else None

    async def wait_song(self, pred, timeout: float = 6.0) -> tuple[dict | None, float | None]:
        t0 = time.time()
        s = None
        while time.time() - t0 < timeout:
            s = await self.song()
            if s is not None and pred(s):
                return s, time.time() - t0
            await asyncio.sleep(0.2)
        return s, None

    async def finish(self) -> dict:
        if self._task:
            self._task.cancel()
        out = {}
        if not getattr(self, "real", True):
            await self.admin.close()
            self.final = {"real_pear": "not touched (mock-only groups)"}
            return self.final
        try:
            await self.set_volume(0)
            for attempt in range(4):
                await self.admin.pause()
                s, _ = await self.wait_song(lambda s: s.get("isPaused"), 3)
                if s is None or s.get("isPaused"):
                    break
                # seen 19 Oct: an unplayable video (elapsed stuck at 0) ignores pause -> skip it (volume 0), pause again
                out.setdefault("pause_workaround", []).append(f"skipped stuck {s.get('videoId')}")
                await self.admin.next()
                await asyncio.sleep(3)
            out["paused"] = bool((await self.song() or {"isPaused": True}).get("isPaused"))
        except Exception as e:
            out["pause_error"] = repr(e)
        try:
            if out.get("paused"):
                out["volume_posted"] = int(self.restore_volume or 0)
                out["volume"] = await self.set_volume(int(self.restore_volume or 0))
            else:
                out["volume"] = "left at 0 because Pear could not be paused"
        except Exception as e:
            out["volume_error"] = repr(e)
        out["max_volume_seen_during_tests"] = self.max_seen
        out["volume_violations"] = len(self.violations)
        print(f"[safety] restored: {out}")
        await self.admin.close()
        self.final = out
        return out


# ======================================================================== scenario framework
@dataclass
class R:
    passed: bool
    key: str                     # the key number, e.g. "confirm 2.1 s"
    detail: str = ""
    evidence: list = field(default_factory=list)
    data: dict = field(default_factory=dict)


SCEN: list[tuple[str, str, str, object]] = []      # (id, group, name, fn)


def scenario(sid: str, group: str, name: str):
    def deco(fn):
        SCEN.append((sid, group, name, fn))
        return fn
    return deco


class Ctx:
    def __init__(self, safety: Safety, args) -> None:
        self.s = safety
        self.args = args
        self.tmp = Path(tempfile.mkdtemp(prefix="mock-", dir=safety.dir))
        self.lt = LogTransport()
        self.player = PearDesktopPlayer(PEAR, safety.paired_copy(), 5.0, transport=self.lt)
        self.reg, self.skill = reg_for(self.player, max_volume=SAFE_MAX_VOL)

    async def call(self, tool: str, args: dict | None = None):
        t = time.perf_counter()
        res = await self.reg.call(tool, args or {})
        return res, time.perf_counter() - t

    async def play_check(self, query: str, title_kw: str | None = None, artist_kw: str | None = None) -> R:
        res, dt = await self.call("music_play", {"query": query})
        np_ = (res.data or {}).get("now_playing") or {}
        song = await self.s.song() or {}
        ev = [f"tool: ok={res.ok} code={res.code} msg={res.message!r} ({dt:.1f} s)",
              f"pear /song: title={song.get('title')!r} artist={song.get('artist')!r} id={song.get('videoId')} "
              f"paused={song.get('isPaused')}"]
        ok = res.ok and not song.get("isPaused", True) and norm(song.get("title")) == norm(np_.get("title"))
        if title_kw:
            ok = ok and norm(title_kw) in norm(song.get("title"))
        if artist_kw:
            ok = ok and norm(artist_kw) in norm(song.get("artist") + " " + song.get("title", ""))
        return R(ok, f"confirm {dt:.1f} s", f"{song.get('title')} – {song.get('artist')}", ev,
                 {"latency_s": round(dt, 2), "title": song.get("title"), "artist": song.get("artist"),
                  "searched_id": ((res.data or {}).get("track") or {}).get("video_id"), "played_id": song.get("videoId")})


# ------------------------------------------------------------------------ skill level, REAL Pear
@scenario("S01", "skill", "play Hebrew artist: שלמה ארצי")
async def s01(c: Ctx) -> R:
    return await c.play_check("שלמה ארצי", artist_kw="ארצי")


@scenario("S02", "skill", "play Hebrew artist: עומר אדם")
async def s02(c: Ctx) -> R:
    return await c.play_check("עומר אדם", artist_kw="עומר אדם")


@scenario("S03", "skill", "play English artist+song: Queen Bohemian Rhapsody")
async def s03(c: Ctx) -> R:
    return await c.play_check("Queen Bohemian Rhapsody", title_kw="Bohemian Rhapsody", artist_kw="Queen")


@scenario("S04", "skill", "play English artist: Beatles")
async def s04(c: Ctx) -> R:
    return await c.play_check("Beatles", artist_kw="Beatles")


@scenario("S05", "skill", "play a mood: משהו רגוע")
async def s05(c: Ctx) -> R:
    return await c.play_check("משהו רגוע")


@scenario("S06", "skill", "play a different song while one is playing (queue-index fix)")
async def s06(c: Ctx) -> R:
    first = await c.play_check("Queen Bohemian Rhapsody", title_kw="Bohemian")
    q = await c.player.queue()
    cur = next((i for i, (_, sel) in enumerate(q) if sel), -1)
    old_next = q[cur + 1][0] if 0 <= cur < len(q) - 1 else None
    r = await c.play_check("Eagles Hotel California", title_kw="Hotel California")
    song = await c.s.song() or {}
    ok = first.passed and r.passed and song.get("videoId") != old_next
    return R(ok, r.key, f"old next={old_next}, now={song.get('videoId')} {song.get('title')!r}",
             first.evidence + r.evidence + [f"queue len {len(q)}, selected {cur}, old next {old_next}"])


@scenario("S07", "skill", "pause, resume, next, stop vs Pear state")
async def s07(c: Ctx) -> R:
    ev, ok, keys = [], True, []
    base = await c.s.song() or {}
    if base.get("isPaused", True):
        await c.play_check("Coldplay Yellow")
        base = await c.s.song() or {}
    steps = [("music_pause", lambda s: s.get("isPaused")),
             ("music_resume", lambda s: not s.get("isPaused")),
             ("music_next", lambda s: s.get("videoId") != base.get("videoId") and not s.get("isPaused")),
             ("music_stop", lambda s: s.get("isPaused"))]
    for tool, pred in steps:
        res, dt = await c.call(tool)
        s, lat = await c.s.wait_song(pred, 6)
        good = res.ok and lat is not None
        ok &= good
        keys.append(f"{tool[6:]} {lat:.1f}s" if lat is not None else f"{tool[6:]} FAIL")
        ev.append(f"{tool}: ok={res.ok} msg={res.message!r} tool {dt:.2f}s; pear reached state after "
                  f"{'%.2f' % lat if lat is not None else 'never'} s: {(s or {}).get('title')!r} paused={(s or {}).get('isPaused')}")
    return R(ok, ", ".join(keys), "", ev)


@scenario("S08", "skill", "status tool text matches Pear (playing and paused)")
async def s08(c: Ctx) -> R:
    ev, ok = [], True
    await c.call("music_resume")
    await c.s.wait_song(lambda s: not s.get("isPaused"), 5)
    for want_paused in (False, True):
        if want_paused:
            await c.call("music_pause")
            await c.s.wait_song(lambda s: s.get("isPaused"), 5)
        res, dt = await c.call("music_status")
        s = await c.s.song() or {}
        word = "מושהה" if s.get("isPaused") else "מתנגן"
        good = res.ok and s.get("title", "\0") in res.message and word in res.message and bool(s.get("isPaused")) == want_paused
        ok &= good
        ev.append(f"status={res.message!r} ({dt:.2f}s) vs pear title={s.get('title')!r} paused={s.get('isPaused')}")
    return R(ok, "2/2 states" if ok else "mismatch", "", ev)


@scenario("S09", "skill", "volume 0 and 2 on real Pear; cap enforced (fake player, max 70)")
async def s09(c: Ctx) -> R:
    ev, ok = [], True
    for v in (2, 0):
        res, dt = await c.call("music_volume", {"percent": v})
        t = time.time()
        st = await c.s.volume()
        while time.time() - t < 3 and not (v == 0 and st.get("state") == 0 and st.get("isMuted")):
            await asyncio.sleep(0.2)
            st = await c.s.volume()
        # POST = slider position, GET = effective level on a lower scale: 2 reads back as 0 (unmuted), 0 as 0 muted
        good = res.ok and res.data.get("volume") == v and st.get("state", 99) <= SAFE_MAX_VOL and \
            (v != 0 or (st.get("state") == 0 and st.get("isMuted")))
        ok &= good
        ev.append(f"real: asked {v}: {res.message!r}; pear reports {st} after {time.time() - t:.1f}s")
    res, _ = await c.call("music_volume", {"percent": 50})          # the real skill is capped at SAFE_MAX_VOL
    st = await c.s.volume()
    ev.append(f"real, max_volume={SAFE_MAX_VOL}: asked 50 -> {res.message!r}, data {res.data}")
    ok &= res.ok and res.data.get("volume") == SAFE_MAX_VOL
    await c.s.set_volume(0)
    fp = FakePlayer()
    freg, _ = reg_for(fp, max_volume=70)
    for ask, want, code in ((100, 70, "ok"), (71, 70, "ok"), (70, 70, "ok"), (50, 50, "ok"), (0, 0, "ok"),
                            (150, None, "TOOL_BAD_ARGS"), (-1, None, "TOOL_BAD_ARGS")):
        before = fp.volume
        r = await freg.call("music_volume", {"percent": ask})
        good = r.code == code and (fp.volume == want if want is not None else fp.volume == before)
        ok &= good
        ev.append(f"fake: asked {ask}: code={r.code} msg={r.message!r} player volume {fp.volume} {'OK' if good else 'BAD'}")
    return R(ok, "cap 70 held (7/7)" if ok else "cap/volume FAIL", "", ev)


@scenario("S10", "skill", "rapid play -> next -> pause -> resume -> next (no waits)")
async def s10(c: Ctx) -> R:
    ev, ok = [], True
    t0 = time.perf_counter()
    res, dt = await c.call("music_play", {"query": "Coldplay Viva La Vida"})
    ev.append(f"play: ok={res.ok} {res.message!r} {dt:.1f}s")
    ok &= res.ok
    after_play = await c.s.song() or {}
    for tool in ("music_next", "music_pause", "music_resume", "music_next"):
        r, dt = await c.call(tool)
        s = await c.s.song() or {}
        ev.append(f"{tool}: ok={r.ok} {r.message!r} {dt:.2f}s -> pear {s.get('title')!r} paused={s.get('isPaused')}")
        ok &= r.ok
    total = time.perf_counter() - t0
    s, lat = await c.s.wait_song(lambda s: not s.get("isPaused") and s.get("videoId") != after_play.get("videoId"), 6)
    st, _ = await c.call("music_status")
    s2 = await c.s.song() or {}
    sync = s2.get("title", "\0") in st.message and ("מתנגן" in st.message) == (not s2.get("isPaused"))
    ev.append(f"settled: pear {s2.get('title')!r} paused={s2.get('isPaused')}; status {st.message!r}")
    ok &= lat is not None and sync
    return R(ok, f"sequence {total:.1f} s, in sync={sync}", "", ev)


QUERIES10 = [("Adele Hello", "Adele"), ("Coldplay Yellow", "Coldplay"),
             ("אריק איינשטיין אוהב להיות בבית", "איינשטיין|Einstein"), ("Michael Jackson Billie Jean", "Billie Jean"),
             ("שלום חנוך", "חנוך|Hanoch"), ("Daft Punk Get Lucky", "Get Lucky"), ("עידן רייכל", "רייכל|Raichel"),
             ("Eminem Lose Yourself", "Lose Yourself"), ("נועה קירל", "קירל|Kirel"), ("ABBA Dancing Queen", "Dancing Queen")]


@scenario("S11", "skill", "10 different searches back to back (confirm latency each)")
async def s11(c: Ctx) -> R:
    ev, lats, good, counterpart = [], [], 0, 0
    for q, kw in QUERIES10:
        r = await c.play_check(q)
        rel = any(norm(k) in norm(f"{r.data.get('title')} {r.data.get('artist')}") for k in kw.split("|"))
        if r.passed and rel:
            good += 1
        lats.append(r.data["latency_s"])
        ev.append(f"{q!r}: {'OK' if r.passed else 'FAIL'} rel={'yes' if rel else 'NO'} {r.data['latency_s']} s -> "
                  f"{r.data.get('title')!r} / {r.data.get('artist')!r} | {r.evidence[0]}")
    lat_s = ", ".join(f"{x:.1f}" for x in lats)
    return R(good == len(QUERIES10), f"{good}/10 ok; median {sorted(lats)[len(lats) // 2]:.1f} s, max {max(lats):.1f} s",
             f"latencies: {lat_s}", ev, {"latencies": lats})


@scenario("S12", "skill", "nonsense query -> honest 'not found'")
async def s12(c: Ctx) -> R:
    before = await c.s.song() or {}
    res, dt = await c.call("music_play", {"query": "asdkjhqwe zzxxcc"})
    after = await c.s.song() or {}
    ev = [f"tool: ok={res.ok} code={res.code} msg={res.message!r} data={str(res.data)[:200]} ({dt:.1f}s)",
          f"pear before {before.get('title')!r} -> after {after.get('title')!r}"]
    ok = not res.ok and res.code == "MUSIC_NOT_FOUND" and he(res.message)
    return R(ok, f"{res.code} in {dt:.1f} s", res.message, ev)


@scenario("S13", "skill", "pause / next / resume / status when nothing is playing -> graceful")
async def s13(c: Ctx) -> R:
    ev, ok = [], True
    fp = FakePlayer()
    freg, _ = reg_for(fp)
    for tool in ("music_pause", "music_next", "music_resume", "music_stop", "music_status"):
        r = await freg.call(tool)
        claims = r.message in ("השיר הבא.", "ממשיך לנגן.")     # a claim of an action that did not happen
        good = he(r.message) and not claims and r.code != "TOOL_CRASH"
        ok &= good
        ev.append(f"fake (nothing playing) {tool}: ok={r.ok} code={r.code} {r.message!r} {'OK' if good else 'BAD'}")
    # real Pear: pause while already paused
    await c.call("music_pause")
    await c.s.wait_song(lambda s: s.get("isPaused"), 4)
    r, dt = await c.call("music_pause")
    s = await c.s.song() or {}
    ev.append(f"real pause-while-paused: ok={r.ok} {r.message!r}; pear paused={s.get('isPaused')}")
    ok &= r.ok and bool(s.get("isPaused"))
    return R(ok, "5/5 graceful" if ok else "misleading reply", "", ev)


# ------------------------------------------------------------------------ error honesty
@scenario("E01", "errors", "Pear unreachable (port 26599) -> MUSIC_PLAYER_OFFLINE, Hebrew")
async def e01(c: Ctx) -> R:
    p = PearDesktopPlayer("http://127.0.0.1:26599", c.s.paired_copy(), 3.0)
    reg, sk = reg_for(p)
    ev, ok = [], True
    for tool, a in (("music_play", {"query": "שלמה ארצי"}), ("music_pause", {}), ("music_status", {})):
        t = time.perf_counter()
        r = await reg.call(tool, a)
        dt = time.perf_counter() - t
        good = r.code == "MUSIC_PLAYER_OFFLINE" and not r.ok and he(r.message) and dt < 6
        ok &= good
        ev.append(f"{tool}: code={r.code} {r.message!r} {dt:.2f}s")
    h = await sk.health()
    ev.append(f"health: {h}")
    ok &= h[0] == "unavailable"
    await p.close()
    return R(ok, "3/3 offline + health unavailable" if ok else "FAIL", "", ev)


@scenario("E02", "errors", "unpaired id at the real port -> MUSIC_NOT_PAIRED, no /auth (no dialog)")
async def e02(c: Ctx) -> R:
    d = Path(tempfile.mkdtemp(prefix="unpaired-", dir=c.s.dir))
    idf = d / "pear_client_id"
    idf.write_text("jarves-suite-unpaired-" + secrets.token_hex(8), encoding="utf-8")
    lt = LogTransport()
    p = PearDesktopPlayer(PEAR, idf, 3.0, transport=lt)
    reg, sk = reg_for(p)
    ev, ok = [], True
    for tool, a in (("music_play", {"query": "שלמה ארצי"}), ("music_next", {}), ("music_status", {})):
        r = await reg.call(tool, a)
        good = r.code == "MUSIC_NOT_PAIRED" and he(r.message)
        ok &= good
        ev.append(f"{tool}: code={r.code} {r.message!r}")
    h = await sk.health()
    auth = [x for x in lt.log if "/auth" in x[2]]
    ev.append(f"health: {h}; requests: {[(m, pth, st) for _, m, pth, st in lt.log]}")
    ok &= not auth and h[0] == "degraded" and not idf.with_name(idf.name + ".paired").exists()
    await p.close()
    if not ok and h[0] == "unavailable":       # Pear closed: this scenario needs the real port
        return R(False, "BLOCKED: Pear Desktop not running (real port needed)", "", ev)
    return R(ok, f"/auth calls {len(auth)}, {len(lt.log)} requests", "", ev)


@scenario("E03", "errors", "401 -> silent re-pair (mock), no loop; 403 on /auth -> PAIR_DENIED")
async def e03(c: Ctx) -> R:
    ev, ok = [], True
    mp = MockPear()
    p = mock_player(mp, c.tmp)
    reg, _ = reg_for(p, verify_tries=2)
    assert (await reg.call("music_play", {"query": "Song One"})).ok
    mp.token = "tok2"                              # Pear restarted: old token now 401
    n0 = len(mp.log)
    r = await reg.call("music_status")
    seq = mp.log[n0:]
    good = r.ok and "Song One" in r.message and sum(1 for x in seq if x[1].startswith("/auth/")) == 1
    ok &= good
    ev.append(f"token rotated: status ok={r.ok} {r.message!r}; requests {seq}")
    mp.token = "tok3"                              # held token now invalid, and /auth only hands out a stale one
    mp.faults["POST /auth/jarves-mock"] = lambda req: httpx.Response(200, json={"accessToken": "stale"})
    n0 = len(mp.log)
    r = await reg.call("music_status")
    seq = mp.log[n0:]
    good = not r.ok and r.code == "MUSIC_PLAYER_ERROR" and he(r.message) and len(seq) <= 4
    ok &= good
    ev.append(f"always 401: code={r.code} {r.message!r}; {len(seq)} requests (no loop) {seq}")
    mp.token = "tok4"
    mp.faults["POST /auth/jarves-mock"] = lambda req: httpx.Response(403)
    marker = p.id_file.with_name(p.id_file.name + ".paired")
    r = await reg.call("music_status")
    good = r.code == "MUSIC_PAIR_DENIED" and he(r.message) and not marker.exists()
    ok &= good
    ev.append(f"403 on /auth: code={r.code} {r.message!r}; marker removed={not marker.exists()}")
    r = await reg.call("music_status")
    ev.append(f"after deny (no marker): code={r.code} {r.message!r}; auth calls after: "
              f"{[x for x in mp.log[-3:] if x[1].startswith('/auth')]}")
    ok &= r.code == "MUSIC_NOT_PAIRED"
    return R(ok, "re-pair 1 auth; no loop; deny honoured" if ok else "FAIL", "", ev)


# ------------------------------------------------------------------------ robustness (mock transports)
@scenario("R01", "robust", "search edge cases: empty, no videoIds, malformed JSON, 500, timeout, 204")
async def r01(c: Ctx) -> R:
    ev, ok = [], True
    cases = [
        ("empty results", {"search_override": {"contents": {}}}, {"MUSIC_NOT_FOUND"}),
        ("only artist/browse results", {"search_override": {"contents": {"musicCardShelfRenderer": {"title": {
            "runs": [{"text": "Artist", "navigationEndpoint": {"browseEndpoint": {"browseId": "UC1"}}}]}}}}}, {"MUSIC_NOT_FOUND"}),
        ("malformed JSON", {"search_override": httpx.Response(200, content=b"{not json")}, {"MUSIC_PLAYER_ERROR"}),
        ("JSON array", {"search_override": httpx.Response(200, json=[1, 2])}, {"MUSIC_NOT_FOUND", "MUSIC_PLAYER_ERROR"}),
        ("HTTP 500", {"search_override": httpx.Response(500, content=b"boom")}, {"MUSIC_PLAYER_ERROR"}),
        ("HTTP 204 empty", {"search_override": httpx.Response(204)}, {"MUSIC_NOT_FOUND", "MUSIC_PLAYER_ERROR"}),
        ("timeout", {"fault": ("POST /api/v1/search", httpx.ReadTimeout("slow"))}, {"MUSIC_TIMEOUT"}),
        ("connection reset", {"fault": ("POST /api/v1/search", httpx.ConnectError("reset"))}, {"MUSIC_PLAYER_OFFLINE"}),
    ]
    for name, setup, want in cases:
        mp = MockPear()
        if "search_override" in setup:
            mp.search_override = setup["search_override"]
        if "fault" in setup:
            mp.faults[setup["fault"][0]] = setup["fault"][1]
        reg, _ = reg_for(mock_player(mp, c.tmp), verify_tries=2)
        r = await reg.call("music_play", {"query": "Song One"})
        good = not r.ok and r.code in want and he(r.message)
        ok &= good
        ev.append(f"{name}: code={r.code} {r.message!r} {'OK' if good else 'BAD (want ' + '/'.join(want) + ')'}")
    return R(ok, f"{sum('OK' in e for e in ev)}/{len(cases)} sensible", "", ev)


@scenario("R02", "robust", "now_playing edge cases: 204, malformed, missing fields, Unicode/RTL titles")
async def r02(c: Ctx) -> R:
    ev, ok = [], True
    mp = MockPear()
    reg, _ = reg_for(mock_player(mp, c.tmp), verify_tries=2)
    r = await reg.call("music_status")
    ok &= r.ok and "שום דבר" in r.message
    ev.append(f"204 (nothing): {r.code} {r.message!r}")
    for name, body, expect_ok in (
            ("malformed JSON", httpx.Response(200, content=b"<html>"), False),
            ("missing fields", httpx.Response(200, json={}), True),
            ("null fields", httpx.Response(200, json={"title": None, "artist": None, "videoId": None, "isPaused": None,
                                                      "elapsedSeconds": None, "songDuration": None}), True),
            ("string durations", httpx.Response(200, json={"title": "X", "artist": "Y", "videoId": "v", "isPaused": False,
                                                           "elapsedSeconds": "12", "songDuration": "n/a"}), True)):
        mp.faults["GET /api/v1/song"] = body
        r = await reg.call("music_status")
        good = r.code != "TOOL_CRASH" and he(r.message) and (r.ok == expect_ok)
        ok &= good
        ev.append(f"{name}: ok={r.ok} code={r.code} {r.message!r} {'OK' if good else 'BAD'}")
    mp.faults.clear()
    rtl = "‏שיר‏ של ‫אהבה‬ 🎵 (Live)"
    mp.catalog.append(("vrtl", rtl, "ארטיסט ‎A‎"))
    mp.info["vrtl"] = (rtl, "ארטיסט ‎A‎")
    r = await reg.call("music_play", {"query": "אהבה"})
    s = await reg.call("music_status")
    good = r.ok and rtl in r.message and rtl in s.message
    ok &= good
    ev.append(f"RTL/emoji title: play ok={r.ok} {r.message!r}; status {s.message!r}")
    # bidi marks differ between search result and player title (same song)
    mp2 = MockPear([("vb", "‏שני משוגעים", "עומר אדם")])
    mp2.counterpart["vb"] = ("vb_song", "שני  משוגעים‏")
    reg2, _ = reg_for(mock_player(mp2, c.tmp), verify_tries=2)
    r = await reg2.call("music_play", {"query": "משוגעים"})
    ok &= r.ok
    ev.append(f"bidi marks / spaces differ (other id): ok={r.ok} code={r.code} {r.message!r}")
    return R(ok, "no crash, RTL kept" if ok else "FAIL", "", ev)


@scenario("R03", "robust", "video result vs its 'song' counterpart (other videoId / title suffix)")
async def r03(c: Ctx) -> R:
    ev, ok = [], True
    cases = [("same title, other id", "Bohemian Rhapsody", ("song1", "Bohemian Rhapsody"), True),
             ("video title has '(Official Video)'", "Bohemian Rhapsody (Official Video)", ("song1", "Bohemian Rhapsody"), True),
             ("'[Official Music Video]' + remaster", "Hotel California [Official Music Video]", ("s2", "Hotel California (2013 Remaster)"), True),
             ("a different song entirely", "Bohemian Rhapsody", ("other", "Another One Bites the Dust"), False)]
    for name, searched_title, played, want in cases:
        mp = MockPear([("vid1", searched_title, "Queen")])
        mp.counterpart["vid1"] = played
        reg, _ = reg_for(mock_player(mp, c.tmp), verify_tries=2)
        r = await reg.call("music_play", {"query": searched_title.split()[0]})
        good = r.ok == want and (want or r.code == "MUSIC_NOT_CONFIRMED")
        ok &= good
        ev.append(f"{name}: searched {searched_title!r} played {played[1]!r}: ok={r.ok} code={r.code} {r.message!r} "
                  f"{'OK' if good else 'BAD'}")
    return R(ok, f"{sum('OK' in e for e in ev)}/{len(cases)} honest", "", ev)


@scenario("R04", "robust", "queue endpoint broken / slow -> play still works or fails honestly")
async def r04(c: Ctx) -> R:
    ev, ok = [], True
    for name, fault, want_ok in (("queue malformed", httpx.Response(200, content=b"xx"), True),
                                 ("queue 500", httpx.Response(500), None),
                                 ("queue empty body", httpx.Response(200, content=b""), True)):
        mp = MockPear()
        mp.faults["GET /api/v1/queue"] = fault
        reg, _ = reg_for(mock_player(mp, c.tmp), verify_tries=2)
        t = time.perf_counter()
        r = await reg.call("music_play", {"query": "Song One"})
        dt = time.perf_counter() - t
        good = r.code != "TOOL_CRASH" and he(r.message) and (want_ok is None or r.ok == want_ok)
        ok &= good
        ev.append(f"{name}: ok={r.ok} code={r.code} {r.message!r} {dt:.1f}s {'OK' if good else 'BAD'}")
    return R(ok, "no crash" if ok else "FAIL", "", ev)


# ------------------------------------------------------------------------ soak
def rss_kb() -> int:
    """This process's working set (KB), Windows; tracemalloc slows the JSON-heavy calls about 4x."""
    import ctypes
    from ctypes import wintypes as w

    class PMC(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD)] + [(n, ctypes.c_size_t) for n in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return int(pmc.WorkingSetSize // 1024)
    except Exception:
        return 0


@scenario("K01", "soak", "long soak: alternating play/next/pause/resume/status at volume 0")
async def k01(c: Ctx) -> R:
    minutes = c.args.soak_min
    queries = ["שלמה ארצי", "Queen", "עומר אדם", "Beatles Let It Be", "Coldplay", "אריק איינשטיין", "Adele", "משהו רגוע"]
    cycle = ["play", "status", "next", "status", "pause", "status", "resume", "status", "next", "status"]
    m0 = rss_kb()
    objs0 = len(gc.get_objects())
    tasks0 = len(asyncio.all_tasks())
    q0 = len(await c.player.queue())
    t_end = time.time() + minutes * 60
    n = errors = mismatches = plays = 0
    lat: dict[str, list[float]] = {}
    ev, qlens, mems = [], [], []
    qi = 0
    while time.time() < t_end:
        for op in cycle:
            if time.time() >= t_end:
                break
            if op == "play":
                res, dt = await c.call("music_play", {"query": queries[qi % len(queries)]})
                qi += 1
                plays += 1
            else:
                res, dt = await c.call(f"music_{op}")
            n += 1
            lat.setdefault(op, []).append(dt)
            print(f"   soak #{n} {op}: {res.code} {dt:.1f}s {res.message[:60]!r}", flush=True)
            if not res.ok:
                errors += 1
                ev.append(f"#{n} {op}: {res.code} {res.message!r}")
            await asyncio.sleep(2.0)
            s = await c.s.song() or {}
            exp_paused = {"pause": True, "resume": False, "play": False, "next": False}.get(op)
            if exp_paused is not None and bool(s.get("isPaused")) != exp_paused:
                s, _ = await c.s.wait_song(lambda x: bool(x.get("isPaused")) == exp_paused, 4)
                if s is None or bool(s.get("isPaused")) != exp_paused:
                    mismatches += 1
                    ev.append(f"#{n} {op}: pear paused={(s or {}).get('isPaused')} expected {exp_paused}")
            if op == "status" and res.ok and s.get("title") and s["title"] not in res.message:
                mismatches += 1
                ev.append(f"#{n} status {res.message!r} but pear {s.get('title')!r}")
        qlens.append(len(await c.player.queue()))
        gc.collect()
        mems.append(rss_kb() - m0)
    tasks1 = len(asyncio.all_tasks())
    objs1 = len(gc.get_objects())
    mem_growth_kb = (mems[-1] - mems[min(1, len(mems) - 1)]) if mems else 0
    q_growth = (qlens[-1] - q0) if qlens else 0
    ok = errors == 0 and mismatches == 0 and tasks1 <= tasks0 + 1 and mem_growth_kb < 30_000 and \
        q_growth <= plays + 60 and (max(qlens) if qlens else 0) < 500
    lat_s = {k: f"median {sorted(v)[len(v) // 2]:.2f}s max {max(v):.2f}s" for k, v in lat.items()}
    return R(ok, f"{n} ops in {minutes:g} min, {errors} errors, {mismatches} desyncs",
             f"queue {q0} -> {qlens[-1] if qlens else '?'} (max {max(qlens) if qlens else '?'}, {plays} plays); "
             f"process memory growth {mem_growth_kb:.0f} KB (cycle 2 -> end); python objects {objs0}->{objs1}; "
             f"tasks {tasks0}->{tasks1}",
             ev[:20] + [f"latency {lat_s}", f"queue lengths per cycle {qlens}",
                        f"working-set growth per cycle (KB) {mems}"],
             {"ops": n, "errors": errors, "mismatches": mismatches, "queue": qlens, "lat": lat_s})


# ======================================================================== voice (real GPT-Live, virtual audio)
class VirtualAudio:
    """Copied from tools/silent_live.py: mic = injected speech (+ faint noise); speaker = measured, never played."""
    inject: dict[int, bytearray] = {16000: bytearray(), 24000: bytearray()}
    play_log: list[tuple[float, float, float]] = []
    last_play = 0.0

    def __init__(self, *a, **k) -> None:
        self._q: deque[bytes] = deque()
        self._bytes = 0
        self._ready = asyncio.Event()
        self._open = True
        self._rng = np.random.default_rng(1)

    async def mic_chunks(self, rate: int, chunk_bytes: int):
        buf = VirtualAudio.inject[rate]
        while self._open:
            await asyncio.sleep(chunk_bytes / 2 / rate)
            take = bytes(buf[:chunk_bytes])
            del buf[:chunk_bytes]
            a = np.frombuffer(take.ljust(chunk_bytes, b"\0"), "<i2").astype(np.int32)
            if len(take) < chunk_bytes:
                a = a + self._rng.integers(-6, 7, len(a))
            yield np.clip(a, -32768, 32767).astype("<i2").tobytes()

    def enqueue(self, pcm: bytes) -> None:
        if self._open and pcm:
            self._q.append(pcm)
            self._bytes += len(pcm)
            self._ready.set()

    def flush(self) -> int:
        n = self._bytes
        self._q.clear()
        self._bytes = 0
        return n

    def queued_s(self) -> float:
        return self._bytes / 2 / 24000

    async def run_player(self, rate: int) -> None:
        while self._open:
            if not self._q:
                self._ready.clear()
                await self._ready.wait()
                continue
            pcm = self._q.popleft()
            self._bytes -= len(pcm)
            for i in range(0, len(pcm), rate // 10 * 2):
                piece = pcm[i:i + rate // 10 * 2]
                a = np.frombuffer(piece, "<i2").astype(np.float32) / 32768.0
                now = time.time()
                VirtualAudio.play_log.append((now, len(piece) / 2 / rate, float(np.sqrt(np.mean(a * a)))))
                VirtualAudio.last_play = now
                await asyncio.sleep(len(piece) / 2 / rate)

    def stats(self) -> dict:
        return {"mic_drops": 0, "mic_max_depth_s": 0, "speaker_drops": 0, "speaker_dropped_s": 0, "speaker_max_depth_s": 0,
                "speaker_depth_s": round(self.queued_s(), 2), "speaker_flushes": 0, "speaker_flushed_s": 0}

    def close(self) -> None:
        self._open = False
        self._ready.set()


def to_pcm(samples: np.ndarray, src_rate: int, dst_rate: int) -> bytes:
    n = int(len(samples) * dst_rate / src_rate)
    out = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    out = out / max(1e-6, float(np.abs(out).max())) * 0.6
    return (out * 32767).astype("<i2").tobytes()


class FaceClient:
    def __init__(self, home: Path, port: int) -> None:
        self.home, self.port, self.msgs, self._task = home, port, [], None

    async def start(self) -> None:
        from websockets.asyncio.client import connect
        self.ws = await connect(f"ws://127.0.0.1:{self.port}")
        token = (self.home / "link_token").read_text(encoding="utf-8").strip()
        await self.ws.send(json.dumps({"type": "hello", "payload": {"token": token, "client": "godot", "protocol": 1}}))
        self._task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                self.msgs.append((time.time(), m["type"], m.get("payload") or {}))
        except Exception:
            pass

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.ws.close()


class _NoSTT:
    loaded_name = "none"

    async def transcribe(self, audio):
        return ""


class _NoTTS:
    def check(self):
        return True, "cloud", "silent test"

    async def synth(self, text):
        return np.zeros(2400, np.float32), 24000


def events(home: Path) -> list[dict]:
    try:
        return [json.loads(l) for l in (home / "logs" / "jarves.log").read_text(encoding="utf-8").splitlines()]
    except FileNotFoundError:
        return []


def stamp(e: dict) -> float:
    return datetime.fromisoformat(e["ts"]).timestamp()


async def quiet(sec: float, timeout: float, core) -> None:
    t = time.time()
    while time.time() - t < timeout:
        busy = bool(getattr(core.live, "_backend_busy", False)) if core.live else False
        if time.time() - VirtualAudio.last_play >= sec and not busy and core.live is not None:
            return
        await asyncio.sleep(0.1)


def voice_home(id_file: Path) -> Path:
    home = WORK / "voice-home"
    if home.exists():
        shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True, exist_ok=True)
    from jarves.config import load_secrets
    load_secrets(REAL)                                       # key -> environment only, never printed
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("no OPENAI_API_KEY")
    os.environ["JARVES_HOME"] = str(home)
    cfg = tomllib.loads((REAL / "config.toml").read_text(encoding="utf-8")) if (REAL / "config.toml").exists() else {}
    v = cfg.setdefault("voice", {})
    v.update(activation="conversation", wake_enabled=False, chime=False)
    cfg["link"] = {"port": LINK_PORT}
    cfg["music"] = {"provider": "pear", "max_volume": SAFE_MAX_VOL}
    cfg["log"] = {"level": "VERBOSE", "privacy": False}
    lines = []
    for sec, kv in cfg.items():
        lines.append(f"[{sec}]")
        lines += [f"{k} = " + (json.dumps(x) if isinstance(x, str) else str(x).lower() if isinstance(x, bool) else str(x))
                  for k, x in kv.items()]
        lines.append("")
    (home / "config.toml").write_text("\n".join(lines), encoding="utf-8")
    shutil.copy(id_file, home / "pear_client_id")
    (home / "pear_client_id.paired").write_text("copy for the test suite\n", encoding="utf-8")
    return home


def title_named(title: str, said: str) -> bool:
    words = [w for w in re.findall(r"[\w']+", norm(title)) if len(w) >= 2 and w not in ("feat", "the", "of", "official",
                                                                                          "video", "live", "remastered")]
    if not words:
        return False
    hits = sum(1 for w in words if w in norm(said))
    return hits >= max(1, math.ceil(len(words) / 2))


VOICE_TURNS = [  # (id, name, text, kind)
    ("V01", "voice: 'נגן את שלמה ארצי' -> Pear plays the artist", "נגן את שלמה ארצי", "artist:ארצי"),
    ("V02", "voice: 'עצור' -> paused", "עצור", "paused"),
    ("V03", "voice: 'תמשיך' -> resumed", "תמשיך", "resumed"),
    ("V04", "voice: 'השיר הבא' -> track changed", "השיר הבא", "changed"),
    ("V05", "voice: 'מה מתנגן עכשיו?' -> names the real title", "מה מתנגן עכשיו?", "status"),
    ("V06", "voice: 'נגן משהו של Queen'", "נגן משהו של קווין", "artist:queen"),
    ("V07", "voice: 'תנגן שיר שמח' (vague)", "תנגן שיר שמח", "changed"),
    ("V08", "voice: web question while music plays (mixed)", "מה מזג האוויר היום בתל אביב?", "web"),
    ("V09", "voice: music right after the web question", "עכשיו תנגן את עומר אדם", "artist:עומר אדם"),
]


async def run_voice(c: Ctx, picked: set[str] | None) -> dict[str, R]:
    turns = [t for t in VOICE_TURNS if picked is None or t[0] in picked]
    out: dict[str, R] = {}
    if not turns and (picked is None or "V10" not in picked):
        return out
    home = voice_home(c.s.id_file)
    import jarves.app as app_mod
    from jarves.app import Core
    from jarves.config import load_config
    from jarves.voice.audio import SimAudio
    from jarves.voice.tts import WinRTTTS
    app_mod.StreamingAudio = VirtualAudio
    cfg, _ = load_config()
    assert cfg.link.port == LINK_PORT and cfg.music.max_volume == SAFE_MAX_VOL
    hetts = WinRTTTS("he-IL")
    lt = LogTransport()
    player = PearDesktopPlayer(PEAR, home / "pear_client_id", cfg.music.timeout_s, transport=lt)
    pear_log: list[tuple[float, dict]] = []

    async def poll():
        while True:
            try:
                s = await c.s.song()
                if s:
                    pear_log.append((time.time(), s))
            except Exception:
                pass
            await asyncio.sleep(0.25)

    poller = asyncio.create_task(poll())
    t_start = time.time()
    core = Core(cfg, home, audio=SimAudio(), player=player, console_logs=False, use_real_audio=True, stt=_NoSTT(),
                tts=_NoTTS())
    face = None
    try:
        await core.start()
        await core.prepare_desktop()
        face = FaceClient(home, cfg.link.port)
        await face.start()
        t_wait = time.time()
        while not VirtualAudio.play_log and time.time() - t_wait < 20:
            await asyncio.sleep(0.05)
        await quiet(1.5, 40, core)
        print(f"[voice] connected in {time.time() - t_start:.1f} s; session open {core.live is not None}", flush=True)
        prev_said_title = None
        for sid, name, text, kind in turns:
            if (time.time() - t_start) / 60 > c.args.voice_max_min:
                out[sid] = R(False, "skipped: live minute cap", "")
                continue
            before = await c.s.song() or {}
            samples, rate = await hetts.synth(text)
            mark, fmark = len(events(home)), len(face.msgs)
            for r_ in (16000, 24000):
                VirtualAudio.inject[r_] += to_pcm(samples, rate, r_)
            while VirtualAudio.inject[24000]:
                await asyncio.sleep(0.05)
            t_q_end = time.time()
            first = None
            while time.time() - t_q_end < 60:
                first = next((t for t, d, rm in VirtualAudio.play_log if t >= t_q_end - 0.2 and rm >= 0.004), None)
                if first:
                    break
                await asyncio.sleep(0.05)
            await quiet(2.5, 90, core)
            for _ in range(3):
                done = [stamp(e) for e in events(home)[mark:] if e["code"] == "LIVE_BACKEND_DONE"]
                if not done or VirtualAudio.last_play > max(done):
                    break
                t_w = time.time()
                while VirtualAudio.last_play <= max(done) and time.time() - t_w < 15:
                    await asyncio.sleep(0.1)
                await quiet(2.5, 90, core)
            await asyncio.sleep(1.0)
            ev = events(home)[mark:]
            said = " / ".join(e.get("text", "") for e in ev if e["code"] == "LIVE_JARVES_SAID")
            heard = next((e.get("text") for e in ev if e["code"] == "LIVE_USER_SAID"), None)
            tools = [e["msg"].replace("backend called ", "") for e in ev if e["code"] == "LIVE_TOOL_CALL"]
            results = [(e.get("tool"), e.get("ok"), e["msg"]) for e in ev if e["code"] == "TOOL_RESULT"]
            acts = [m[2] for m in face.msgs[fmark:] if m[1] == "assistant.activity"]
            win = [(t, s) for t, s in pear_log if t >= t_q_end]
            after = await c.s.song() or {}
            bvid = before.get("videoId")

            def pred(s):
                if kind.startswith("artist:"):
                    return not s.get("isPaused") and norm(kind[7:]) in norm(s.get("artist", "") + " " + s.get("title", ""))
                if kind == "paused":
                    return bool(s.get("isPaused"))
                if kind == "resumed":
                    return not s.get("isPaused")
                if kind == "changed":
                    return not s.get("isPaused") and s.get("videoId") != bvid
                return False
            hit = next((t for t, s in win if pred(s)), None)
            t_pear = round(hit - t_q_end, 2) if hit else None
            dead = round(first - t_q_end, 2) if first else None
            if kind == "status":
                passed = title_named(after.get("title", ""), said)
                key = f"named title={passed}, dead air {dead} s"
            elif kind == "web":
                stayed = not after.get("isPaused") and after.get("videoId") == bvid
                passed = bool(said) and stayed and "music_" not in " ".join(tools)
                key = f"answered, music undisturbed={stayed}, dead air {dead} s"
            else:
                passed = pred(after) and hit is not None
                key = f"Pear {t_pear} s, dead air {dead} s"
            out[sid] = R(passed, key, f"heard {heard!r}; said {said[:120]!r}", [
                f"heard as: {heard!r}", f"said: {said[:300]!r}", f"tools: {tools}; results: {results}",
                f"activity: {[(a.get('kind'), a.get('text'), a.get('detail')) for a in acts]}",
                f"pear before: {before.get('title')!r}/{before.get('artist')!r} paused={before.get('isPaused')} -> after: "
                f"{after.get('title')!r}/{after.get('artist')!r} paused={after.get('isPaused')}"],
                {"dead_air_s": dead, "pear_s": t_pear, "activity": acts, "after": after})
            print(f"[voice] {sid} {'PASS' if passed else 'FAIL'} {key} | heard {heard!r} | said {said[:80]!r} | tools {tools}",
                  flush=True)
            await asyncio.sleep(1.5)
        # V10: screen activity 'music' carries the song detail for the play turns
        plays = [sid for sid, _, _, k in turns if k.startswith("artist:") or sid == "V07"]
        good, ev10 = 0, []
        for sid in plays:
            r = out.get(sid)
            if r is None or not r.data:
                continue
            acts = r.data["activity"]
            title = (r.data.get("after") or {}).get("title", "\0")
            m = [a for a in acts if a.get("kind") == "music" and a.get("detail")]
            okk = any(title in (a.get("detail") or "") for a in m)
            good += okk
            ev10.append(f"{sid}: music activity details {[a.get('detail') for a in m]} vs pear {title!r} -> {okk}")
        if picked is None or "V10" in picked:
            out["V10"] = R(bool(plays) and good == len(plays), f"{good}/{len(plays)} play turns showed the song", "", ev10)
        snap = core.snapshot()
        wall = round((time.time() - t_start) / 60, 2)
        print(f"[voice] wall minutes {wall}; voice_minutes {snap.get('voice_minutes_today')}", flush=True)
        for r in out.values():
            r.data.pop("activity", None)
        out["_meta"] = R(True, f"{wall} live wall-minutes", json.dumps({"voice_minutes": snap.get("voice_minutes_today"),
                                                                       "ai_requests": snap.get("ai_requests_today"),
                                                                       "auth_calls": sum('/auth' in x[2] for x in lt.log)}))
    finally:
        poller.cancel()
        if face:
            await face.stop()
        await core.stop()
    return out


# ======================================================================== runner + report
def load_store() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


FINDINGS = [
    "Product bugs found and fixed in `core/src/jarves/skills/music.py` (regressions in `core/tests/test_music_qa.py`):",
    "",
    "1. **Gibberish played a random video and claimed success** (S12). `asdkjhqwe zzxxcc` played a 2.7K-view user "
    "upload; JARVES said 'מנגן: ...'. That video never actually started (elapsed stayed 0) and ignored pause. Fix: "
    "`search_is_relevant()`, which requires at least one query word in the text YouTube shows. Otherwise the answer "
    "is MUSIC_NOT_FOUND.",
    "2. **'next' stayed on the same song** (V04 live, S07, S10). Every repeated request inserted another copy after "
    "the current item, so the real queue held back-to-back copies (19 adjacent duplicates in 77 items) and Pear's "
    "next landed on the same song. Fix: `play_track` reuses a copy that is current or next, and `next()` skips "
    "copies of the current song. `music_next` now waits up to 7 s, presses next once more after 4 s, and names "
    "the new song. If the song still has not changed, it answers MUSIC_NOT_CONFIRMED honestly.",
    "3. **'next' / 'resume' with nothing playing claimed an action** (S13): they answered 'השיר הבא.' or 'ממשיך "
    "לנגן.'. They now answer 'שום דבר לא מתנגן כרגע.' (MUSIC_NOTHING_PLAYING).",
    "4. **Crashes on odd Pear answers** (R01, R02, R04): malformed or empty JSON from search, queue or song, or "
    "non-numeric durations, gave TOOL_CRASH. They now give MUSIC_PLAYER_ERROR or 'not found' with a Hebrew "
    "message. An unreadable queue falls back to the old path.",
    "5. **False 'not confirmed' for the song counterpart** (R02, R03): titles differing only by bidi marks, "
    "'(Official Video)', '[Official Music Video]' or '(2013 Remaster)' did not match. `_same_track` now compares "
    "the cleaned base title; a different song still never matches.",
    "6. **Self-inflicted and caught by the soak:** the first version of the relevance check (fix 1) was quadratic. "
    "It took 8 s per real 250 KB search answer (50 s under tracemalloc), so confirms grew from ~3.5 s to 10-15 s and "
    "every soak play timed out. Rewritten iteratively, it takes 0.007 s; a performance regression test was added. "
    "The 'Pear search is slow' theory was wrong: raw /search answered in 0.5 s.",
    "7. `music_next` timeout 10 s -> 15 s (it now confirms the change, up to 7 s).",
    "8. **Every play kept the PREVIOUS request playing** (one run: S11 0/10, V01 and V06 live). On a ~100-item queue "
    "the list shifted by one between our read and the jump. Fix: `_select()` checks that the selected item is the "
    "song and re-selects it only when the song has really moved. Re-jumping merely because GET /queue still "
    "showed the old 'selected' froze playback (seen once: elapsed stuck, pause ignored). After the fix: S11 10/10.",
    "9. Tried and REVERTED: requiring elapsed > 0 for the confirm. Pear's elapsedSeconds stays 0 while really "
    "playing and is refreshed only on events: 0 for 9 s, then 9 right after a pause. Requiring it gave 0/10 false "
    "'not confirmed'. A regression test keeps it out.",
    "10. `play_track` no longer reads the whole queue first; the 'already playing' check uses /song (the queue "
    "answer reached 2.4 MB and 2.2 s per read).",
    "",
    "Remaining, not fixed (needs a user decision):",
    "",
    "- **Pear slows down as its queue grows.** Each new song is inserted, and nothing is ever removed. After ~250 "
    "test plays the queue held ~170 items (2.4 MB per read). Pear then answered slowly (one 5 s HTTP timeout), "
    "'next' took >7 s, and once it ignored jumps for ~40 s (S04, S07, S10, S11 3/10 in the last run). JARVES "
    "stayed honest ('לא מדווח שהשיר מתנגן'); it never claimed a song that was not playing. With the 32-item queue "
    "at the start of the night: S11 10/10 at 2-4.6 s. Options: (a) JARVES removes already-played items "
    "(DELETE /queue/{i}) to keep the queue under ~50, or (b) it plays via a fresh queue. Both change the user's "
    "queue, so they were not tried tonight. Restarting Pear also clears it.",
    "- **Final 15-min soak (K01) on that 187-207-item queue:** 197 ops; 38 honest failures (mostly 'next' not "
    "reflected within 7.4 s; 4 of 20 plays not confirmed) and 8 pause/resume desyncs. No crash, no false success, "
    "flat memory and asyncio tasks, queue +1 per play. The earlier soak on a shorter queue (pre-fix build) had "
    "only play timeouts, caused by bug 6. A soak on a freshly started Pear is still owed.",
    "- The voice rows V02-V05 and V07-V10 come from runs on earlier builds of this branch; V06 and V10 failed on "
    "the builds with bugs 8 and 9. The live budget (10 min) was used up before a final all-voice rerun. The "
    "causes are fixed and verified at Skill level (S06, S11).",
    "",
    "Environment facts (Pear Desktop 3.12, precise-volume plugin on):",
    "",
    "- **Volume scale:** POST /volume sets the SLIDER; GET reports the effective level on a lower, roughly "
    "quadratic scale. POST 8 reads back 1; POST 2 or 3 read back 0 (unmuted); POST 0 reads back 0 and muted. "
    "Unmuting via toggle-mute restored a hidden level 5. The suite only ever POSTed 0-3 during tests (the cap). "
    "So 'restore to 8' POSTs 8, which reads back about 1: the user may find the music quieter than before. "
    "Where the slider sat when the night began cannot be recovered from the reading of 8 without raising the "
    "volume, which the night rules forbid.",
    "- The queue grows by one item per new song (insert after current); the soak table shows the growth.",
    "- An unplayable user video (seen for the gibberish query) reports isPaused=false with elapsed stuck at 0, and "
    "it ignores pause. The suite's safety step skips it with 'next' at volume 0. JARVES can no longer reach such a "
    "video for gibberish, but a stuck video reached by a real query would still be reported as playing (open item).",
]


def write_report(store: dict, safety_final: dict) -> None:
    order = {sid: i for i, (sid, *_r) in enumerate(SCEN)}
    order.update({t[0]: 100 + i for i, t in enumerate(VOICE_TURNS)})
    order["V10"] = 199
    rows = sorted((k for k in store if not k.startswith("_")), key=lambda k: order.get(k, 999))
    n_pass = sum(store[k]["passed"] for k in rows)
    L = ["# Pre-use test report: music (Pear Desktop / YouTube Music)", "",
         f"Generated {datetime.now():%Y-%m-%d %H:%M} by `core/tools/suite_music.py` against the REAL Pear Desktop on this PC, "
         "silently (Pear volume 0, watchdog cap 3, restored at the end; virtual mic/speaker for voice).", "",
         f"**Result: {n_pass}/{len(rows)} scenarios PASS.**", "",
         "| # | Group | Scenario | Result | Key number | Time | Run at |", "|---|---|---|---|---|---|---|"]
    for k in rows:
        r = store[k]
        L.append(f"| {k} | {r['group']} | {r['name']} | {'PASS' if r['passed'] else '**FAIL**'} | {r['key']} | "
                 f"{r['secs']:.1f} s | {r['at']} |")
    lat = (store.get("S11") or {}).get("data", {}).get("latencies")
    if lat:
        L += ["", "## Confirm latency (search -> Pear reports the song playing), S11", "",
              "| Query | Latency |", "|---|---|"]
        L += [f"| {q} | {x:.1f} s |" for (q, _), x in zip(QUERIES10, lat)]
    vrows = [k for k in rows if k.startswith("V") and store[k].get("data", {}).get("dead_air_s") is not None]
    if vrows:
        L += ["", "## Voice turns: dead air and time until Pear actually played", "",
              "| Turn | Dead air (question end -> first sound) | Pear state reached after |", "|---|---|---|"]
        L += [f"| {k} | {store[k]['data'].get('dead_air_s')} s | {store[k]['data'].get('pear_s')} s |" for k in vrows]
    if "_voice_runs" in store:
        tot = sum(r_["wall_min"] for r_ in store["_voice_runs"])
        L += ["", f"Live GPT-Live usage: {len(store['_voice_runs'])} sessions, {tot:.1f} wall-minutes in total "
                  f"(upper bound of billed voice time; about ${tot * 0.05:.2f} voice + backend tokens). Runs: "
                  + ", ".join(f"{r_['wall_min']} min / {r_['turns']} turns" for r_ in store["_voice_runs"])]
    L += ["", "## Findings (19 Oct 2026)", "", *FINDINGS]
    if safety_final:
        L += ["", "## Safety", "", f"- Final Pear state: `{json.dumps(safety_final, ensure_ascii=False)}`"]
    L += ["", "## Evidence per scenario", ""]
    for k in rows:
        r = store[k]
        L.append(f"### {k} {r['name']} - {'PASS' if r['passed'] else 'FAIL'}")
        if r.get("detail"):
            L.append(f"{r['detail']}")
        L += [f"- {e}" for e in r.get("evidence", [])]
        L.append("")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


def table(rows: list[tuple[str, str, R, float]]) -> None:
    print("\n" + "=" * 110)
    print(f"{'#':5} {'scenario':58} {'result':6} {'time':>7}  key")
    print("-" * 110)
    for sid, name, r, secs in rows:
        print(f"{sid:5} {name[:58]:58} {'PASS' if r.passed else 'FAIL':6} {secs:6.1f}s  {r.key}")
    print("=" * 110)
    print(f"{sum(r.passed for *_x, r, _s in rows)}/{len(rows)} PASS")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default="skill,errors,robust,voice,soak")
    ap.add_argument("--only", help="comma separated scenario ids")
    ap.add_argument("--soak-min", type=float, default=15)
    ap.add_argument("--voice-max-min", type=float, default=8)
    ap.add_argument("--restore-volume", type=int, default=None, help="volume to restore at the end (default: as found)")
    ap.add_argument("--fresh", action="store_true", help="drop earlier stored results")
    ap.add_argument("--report-only", action="store_true", help="rebuild the report from stored results (no Pear)")
    args = ap.parse_args()
    if args.report_only:
        store = load_store()
        write_report(store, store.get("_safety") or {})
        print(f"report: {REPORT}")
        return
    groups = set(args.groups.split(","))
    only = set(args.only.split(",")) if args.only else None
    WORK.mkdir(parents=True, exist_ok=True)
    store = {} if args.fresh else load_store()
    safety = Safety(args.restore_volume)
    # errors/robust use mock transports only: they must run (and are useful) while Pear is closed (P0.4 B-QA02)
    safety.real = bool(groups & {"skill", "voice", "soak"})
    if safety.real:
        await safety.start()
    else:
        print("[safety] mock-only groups: real Pear is not touched")
    rows: list[tuple[str, str, R, float]] = []

    def keep(sid, group, name, r, secs):
        rows.append((sid, name, r, secs))
        store[sid] = {"group": group, "name": name, "passed": r.passed, "key": r.key, "detail": r.detail,
                      "evidence": [str(e) for e in r.evidence], "data": r.data, "secs": secs, "at": f"{datetime.now():%m-%d %H:%M}"}
        STORE.write_text(json.dumps(store, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"{sid} {'PASS' if r.passed else 'FAIL'} {name} | {r.key} | {secs:.1f}s", flush=True)
        if not r.passed:
            for e in r.evidence[:12]:
                print("     ", e, flush=True)

    try:
        ctx = Ctx(safety, args)
        for sid, group, name, fn in SCEN:
            if group not in groups or (only and sid not in only) or group == "soak":
                continue
            t = time.perf_counter()
            try:
                r = await fn(ctx)
            except Exception as e:
                import traceback
                r = R(False, f"CRASH {type(e).__name__}", str(e), traceback.format_exc().splitlines()[-8:])
            keep(sid, group, name, r, time.perf_counter() - t)
        if "voice" in groups:
            vpick = {x for x in only if x.startswith("V")} if only else None
            if vpick is None or vpick:
                t = time.perf_counter()
                try:
                    vres = await run_voice(ctx, vpick)
                except Exception as e:
                    import traceback
                    vres = {"V00": R(False, f"CRASH {type(e).__name__}", str(e), traceback.format_exc().splitlines()[-10:])}
                secs = time.perf_counter() - t
                names = {t_[0]: t_[1] for t_ in VOICE_TURNS} | {"V10": "voice: screen activity 'music' shows the song",
                                                                "V00": "voice session"}
                for sid, r in vres.items():
                    if sid == "_meta":
                        store["_voice_meta"] = {"key": r.key, "detail": r.detail}
                        runs = store.setdefault("_voice_runs", [{"at": "10-19 night", "wall_min": 3.89, "turns": 9},
                                                                {"at": "10-19 night", "wall_min": 0.99, "turns": 2},
                                                                {"at": "10-19 night", "wall_min": 3.04, "turns": 9}])
                        runs.append({"at": f"{datetime.now():%m-%d %H:%M}", "wall_min": float(r.key.split()[0]),
                                     "turns": len(vres) - 1})
                        continue
                    keep(sid, "voice", names.get(sid, sid), r, secs / max(1, len(vres) - 1))
        for sid, group, name, fn in SCEN:
            if group == "soak" and "soak" in groups and (not only or sid in only):
                t = time.perf_counter()
                try:
                    r = await fn(ctx)
                except Exception as e:
                    import traceback
                    r = R(False, f"CRASH {type(e).__name__}", str(e), traceback.format_exc().splitlines()[-8:])
                keep(sid, group, name, r, time.perf_counter() - t)
        await ctx.player.close()
    finally:
        final = await safety.finish()
        if safety.real or "_safety" not in store:      # a mock-only run keeps the last real-Pear safety record
            store["_safety"] = final
        STORE.write_text(json.dumps(store, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        table(rows)
        if safety.real:                        # a mock-only run never rewrites the real-Pear report
            write_report(store, final)
            print(f"report: {REPORT}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
