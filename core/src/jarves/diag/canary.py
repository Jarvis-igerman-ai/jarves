"""Nightly synthetic user-journey canaries on a THROW-AWAY test profile (never the real memory).

Profiles: "ci" (GitHub Actions / tests: every external service is a fake) and "owner" (the owner's PC at 03:30:
the real local engines and services where they exist, read-only, and BLOCKED with a reason where they do not).
Every journey returns PASS / FAIL / BLOCKED with its timing; it plays nothing, sends nothing, writes nothing
outside its own temporary home. The real state folder is only READ (a copy of the Google token).

Journeys:
- voice_stack      a real Core on the test home: virtual mic with a synthetic "Hey Jarvis" (marker detector), the
                   speech-to-text -> command -> text-to-speech round trip on a virtual speaker (measured, never
                   played). GPT-Live connect-and-close only if the owner enabled the paid canary (OD-36).
- music            Pear probe and status read (owner: the real Pear if it runs, else BLOCKED "Pear closed";
                   ci: the music Skill on the fake player); volume forced to 0 first.
- calendar_read    the read-only calendar list on a COPY of the token in the canary home (never a write).
- dashboard_load   real headless Edge / Chromium loads Home, Conversations, Health, Settings: 0 console errors,
                   within the M budget.
- installer_check  every add-on's code / models / data in this install (setup --check-addons) plus the payload
                   hashes against build-info.json when the build lists them.
- bridges          `jarvis agents doctor` and the WhatsApp bridge /status (read-only), when E / F are installed.
- scheduler        a test job due "in 30 s" runs and reports (scheduler clock moved, no real waiting).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"
DASHBOARD_BUDGET_MS = 8000.0         # M budget for one page load (CI-loose); the owner's PC is checked by the monitor
DASHBOARD_PAGES = ("home", "conversations", "health", "settings")


class Blocked(Exception):
    """The journey cannot run here (with the reason); never a FAIL."""


@dataclass
class Env:
    profile: str                      # ci | owner
    home: Path                        # the throw-away canary home
    real_home: Path | None = None     # read-only source of the Google token copy (owner profile)
    paid_voice: bool = False
    browser_channel: str | None = None
    notes: dict = field(default_factory=dict)
    transport: object = None          # tests: an httpx transport for the owner-profile calendar read


# ---------------------------------------------------------------------- journeys
async def j_voice_stack(env: Env) -> str:
    import numpy as np
    from websockets.asyncio.client import connect

    from ..app import Core
    from ..config import Config
    from ..skills.music import FakePlayer
    from ..voice.audio import SimAudio
    from ..voice.wakeword import FakeDetector

    class StubSTT:                    # ci: deterministic; owner: the real local engine (below)
        loaded_name = "canary"

        async def transcribe(self, audio):
            return "מה השעה"

        async def ensure_loaded(self):
            return None

    class MeasuredTTS:                # a virtual speaker: synthesis is measured, never played
        def __init__(self, inner=None):
            self.inner, self.spoken = inner, []

        def check(self):
            return self.inner.check() if self.inner is not None else (True, "cloud", "canary speaker")

        async def synth(self, text):
            self.spoken.append(len(text))
            if self.inner is not None:
                return await self.inner.synth(text)
            return np.zeros(1600, np.float32), 16000

    class Mic:
        def __init__(self):
            self.closed = False

        async def mic_chunks(self, rate, chunk_bytes):
            await asyncio.sleep(0.3)
            yield bytes(chunk_bytes)
            yield FakeDetector.marker_chunk()
            n = 16 * 80
            tone = (np.sin(np.arange(n) * 2 * np.pi * 220 / 16000) * 0.3 * 32767).astype("<i2").tobytes()
            for _ in range(15):
                yield tone
                await asyncio.sleep(0.001)
            while True:
                yield bytes(chunk_bytes)
                await asyncio.sleep(0.01)

        def close(self):
            self.closed = True

    cfg = Config()
    cfg.ai.provider = "offline"
    cfg.voice.mode = "push_to_talk"
    cfg.voice.wake_enabled = True
    cfg.voice.greeting = False
    cfg.voice.greeting_delay_s = 0
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    cfg.link.port = s.getsockname()[1]
    s.close()
    stt, tts_inner = StubSTT(), None
    if env.profile == "owner":
        try:
            from ..voice.stt import WhisperSTT
            stt = WhisperSTT(cfg.voice.stt_model, cfg.voice.stt_fallback_model, cfg.voice.stt_compute, "he")
            await stt.ensure_loaded()
        except Exception as e:
            raise Blocked(f"local speech-to-text not available ({type(e).__name__})")
    tts = MeasuredTTS(tts_inner)
    core = Core(cfg, env.home, audio=SimAudio(), player=FakePlayer(), stt=stt, tts=tts, console_logs=False,
                use_real_audio=False, wake_detector=FakeDetector(), wake_audio=Mic, runtime_mode="foreground")
    await core.start()
    try:
        ws = await connect(f"ws://127.0.0.1:{cfg.link.port}")
        await ws.send(json.dumps({"type": "hello", "payload": {"token": core.link.token, "client": "godot",
                                                               "protocol": 1}}))

        async def wait_text():
            while True:
                m = json.loads(await ws.recv())
                if m["type"] == "assistant.text" and m["payload"].get("source") == "command":
                    return m["payload"]

        await asyncio.wait_for(wait_text(), 60)
        await ws.close()
        if not tts.spoken:
            raise AssertionError("no reply reached the speaker")
        if env.paid_voice:
            env.notes["voice_paid"] = "GPT-Live connect/close canary is enabled but not part of the free path"
        return f"wake -> STT -> reply -> speaker ok ({'stub STT' if env.profile == 'ci' else 'local STT'})"
    finally:
        await core.stop()


async def j_music(env: Env) -> str:
    from ..skills.music import FakePlayer, MusicSkill
    if env.profile == "ci":
        player = FakePlayer()
    else:
        from ..config import Config
        from ..features import _tcp_open
        from ..skills.music import PearDesktopPlayer
        cfg = Config()
        if not _tcp_open(cfg.music.pear_url, timeout=0.5):
            raise Blocked("Pear closed")
        player = PearDesktopPlayer(cfg.music.pear_url, (env.real_home or env.home) / "pear_client_id",
                                   cfg.music.timeout_s)
    skill = MusicSkill(player, 100)
    try:
        await player.set_volume(0) if hasattr(player, "set_volume") else None
        st, detail = await skill.health()
        if st not in ("ok", "degraded"):
            raise AssertionError(f"music status {st}: {detail}")
        return f"status {st}"
    finally:
        await skill.close()


async def j_calendar(env: Env) -> str:
    import httpx

    from ..google_auth import GoogleAuth
    gdir = env.home / "google"
    if env.profile == "ci":
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "token.json").write_text(json.dumps({
            "refresh_token": "canary-refresh", "access_token": "canary-access", "expires_at": time.time() + 3600,
            "scopes": ["https://www.googleapis.com/auth/calendar.events.readonly",
                       "https://www.googleapis.com/auth/calendar.calendarlist.readonly"]}), encoding="utf-8")

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method != "GET":
                return httpx.Response(405)
            if "calendarList" in req.url.path:
                return httpx.Response(200, json={"items": [{"id": "primary", "summary": "<canary>"}]})
            return httpx.Response(404)
        transport = httpx.MockTransport(handler)
    else:
        src = (env.real_home or Path("/nonexistent")) / "google" / "token.json"
        if not src.is_file():
            raise Blocked("Google not connected")
        gdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, gdir / "token.json")       # a COPY: the real token file is never written
        client = src.parent / "client.json"
        if client.is_file():
            shutil.copy2(client, gdir / "client.json")
        transport = env.transport
    auth = GoogleAuth(env.home, transport=transport)
    token = await auth.access_token()
    async with httpx.AsyncClient(transport=transport, timeout=20) as http:
        r = await http.get("https://www.googleapis.com/calendar/v3/users/me/calendarList",
                           headers={"Authorization": f"Bearer {token}"}, params={"maxResults": 5})
    if r.status_code != 200:
        raise AssertionError(f"calendarList HTTP {r.status_code}")
    return f"{len(r.json().get('items', []))} calendar(s) listed (read-only)"


async def j_dashboard(env: Env) -> str:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise Blocked("playwright is not installed (extra 'browser')")
    from ..config import Config
    from ..dashboard import Dashboard
    d = await asyncio.to_thread(lambda: Dashboard(env.home, Config()).start())
    errs: list[str] = []
    times = {}
    try:
        async with async_playwright() as pw:
            kw = {"channel": env.browser_channel} if env.browser_channel else {}
            try:
                browser = await pw.chromium.launch(headless=True, **kw)
            except Exception as e:
                raise Blocked(f"no headless browser ({type(e).__name__})")
            try:
                page = await browser.new_page()
                page.on("pageerror", lambda e: errs.append(f"pageerror {str(e)[:120]}"))
                page.on("console", lambda m: errs.append(f"console {m.text[:120]}") if m.type == "error" else None)
                page.on("response", lambda r: errs.append(f"HTTP {r.status} {r.url[-60:]}") if r.status >= 400 else None)
                for name in DASHBOARD_PAGES:
                    t = time.perf_counter()
                    await page.goto(d.url + "#/" + name)
                    await page.wait_for_load_state("networkidle", timeout=30000)
                    await page.wait_for_function("document.querySelector('#main') && "
                                                 "document.querySelector('#main').innerText.trim().length > 20",
                                                 timeout=30000)
                    times[name] = round((time.perf_counter() - t) * 1000)
            finally:
                await browser.close()
    finally:
        await asyncio.to_thread(d.stop)
    slow = {k: v for k, v in times.items() if v > DASHBOARD_BUDGET_MS}
    if errs or slow:
        raise AssertionError("; ".join(errs[:3] + [f"{k} {v} ms > budget" for k, v in slow.items()]))
    return ", ".join(f"{k} {v} ms" for k, v in times.items())


async def j_installer(env: Env) -> str:
    import hashlib

    from ..setup_wizard import check_addons
    result = await asyncio.to_thread(check_addons)
    rows = result["rows"]
    bad = [r for r in rows if not r.get("ok")] + [{"id": f"load:{m}"} for m in result.get("load_failed") or []]
    if bad:
        raise AssertionError("missing in this install: " + ", ".join(r.get("id", "?") for r in bad[:5]))
    from ..updates import BUILD_INFO
    note = "dev checkout (no build-info payload hashes)"
    try:
        info = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
        hashes = info.get("payload_sha256") or {}
        if hashes:
            root = BUILD_INFO.parent
            wrong = [p for p, h in hashes.items()
                     if not (root / p).is_file() or hashlib.sha256((root / p).read_bytes()).hexdigest() != h]
            if wrong:
                raise AssertionError("payload hash mismatch: " + ", ".join(wrong[:5]))
            note = f"{len(hashes)} payload hash(es) verified"
        else:
            note = "installed build without payload hashes in build-info.json"
    except (OSError, ValueError):
        pass
    return f"{len(rows)} add-on(s) complete; {note}"


async def j_bridges(env: Env) -> str:
    import importlib.util
    have = [m for m in ("jarves.agents", "jarves.whatsapp_bridge") if importlib.util.find_spec(m) is not None]
    if not have:
        raise Blocked("no bridge installed yet (packages E / F)")
    out = []
    for m in have:
        mod = __import__(m, fromlist=["doctor"])
        fn = getattr(mod, "doctor", None) or getattr(mod, "status", None)
        if fn is None:
            raise Blocked(f"{m} has no read-only doctor/status")
        r = fn(env.home)
        r = await r if asyncio.iscoroutine(r) else r
        ok = bool(r.get("ok")) if isinstance(r, dict) else bool(r)
        if not ok:
            raise AssertionError(f"{m} doctor not ok")
        out.append(m.rsplit(".", 1)[-1])
    return "ok: " + ", ".join(out)


async def j_scheduler(env: Env) -> str:
    from ..scheduler import Scheduler
    from ..userdata import open_db
    db = open_db(env.home)
    try:
        now = [time.time()]
        s = Scheduler(db, clock=lambda: now[0])
        v = s.preview("*/5 * * * *", "canary", name="canary job", action="reminder", owner="system", by="canary")
        job = s.approve(v["id"], by="canary")
        now[0] = float(job["next_run"]) + 1       # "due in 30 s": the scheduler clock moves, nobody waits
        await s.tick()
        tasks = [t for ts in s._running.values() for t in ts]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 30)
        runs = db.rows("SELECT state FROM job_runs WHERE job_id=?", (v["id"],))
        if not runs or runs[-1]["state"] not in ("done", "partial"):
            raise AssertionError(f"job run state {runs[-1]['state'] if runs else 'none'}")
        reports = db.rows("SELECT id FROM reports WHERE job_id=?", (v["id"],))
        return f"job ran ({runs[-1]['state']}), {len(reports)} report(s)"
    finally:
        db.close()


JOURNEYS: dict[str, Callable[[Env], object]] = {
    "voice_stack": j_voice_stack, "music": j_music, "calendar_read": j_calendar, "dashboard_load": j_dashboard,
    "installer_check": j_installer, "bridges": j_bridges, "scheduler": j_scheduler}


async def run_journey(name: str, env: Env) -> dict:
    t = time.perf_counter()
    try:
        detail = await JOURNEYS[name](env)
        status = PASS
    except Blocked as e:
        status, detail = BLOCKED, str(e)
    except Exception as e:                       # a journey reports, never takes the runner down
        from .. import logs
        status, detail = FAIL, f"{type(e).__name__}: {logs.redact(str(e))[:200]}"
    return {"name": name, "status": status, "detail": str(detail)[:300], "ms": round((time.perf_counter() - t) * 1000, 1)}


async def run_all(profile: str = "ci", *, real_home: Path | None = None, only: list[str] | None = None,
                  paid_voice: bool = False, browser_channel: str | None = None, transport=None) -> dict:
    """Run the journeys, each in its OWN throw-away home (removed afterwards)."""
    results = []
    t0 = time.perf_counter()
    saved = os.environ.get("JARVES_HOME")
    for name in (only or list(JOURNEYS)):
        tmp = Path(tempfile.mkdtemp(prefix=f"jarves-canary-{name}-"))
        os.environ["JARVES_HOME"] = str(tmp)
        try:
            results.append(await run_journey(name, Env(profile, tmp, real_home, paid_voice, browser_channel,
                                                       transport=transport)))
        finally:
            if saved is None:
                os.environ.pop("JARVES_HOME", None)
            else:
                os.environ["JARVES_HOME"] = saved
            shutil.rmtree(tmp, ignore_errors=True)
    return {"ts": time.time(), "profile": profile, "ms": round((time.perf_counter() - t0) * 1000, 1),
            "passed": sum(r["status"] == PASS for r in results), "failed": sum(r["status"] == FAIL for r in results),
            "blocked": sum(r["status"] == BLOCKED for r in results), "results": results}


def save(store, report: dict) -> int:
    from . import db
    return db.write(store, "INSERT INTO diag_canary (ts, profile, passed, failed, blocked, ms, results) VALUES "
                           "(?,?,?,?,?,?,?)", (report["ts"], report["profile"], report["passed"], report["failed"],
                                               report["blocked"], report["ms"], db.dumps(report["results"])))


def latest(store, n: int = 5) -> list[dict]:
    from . import db
    out = db.rows(store, "SELECT * FROM diag_canary ORDER BY id DESC LIMIT ?", (n,))
    for r in out:
        r["results"] = db.j(r["results"], [])
    return out
