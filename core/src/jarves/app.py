"""JARVES Core: lifecycle, state, Godot face link, DevicePort, brain, voice modes, Skills, health."""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from . import __version__, logs
from .brain import AgentBrain, Brain, billing_routes
from .budget import Budget, BudgetExceeded
from .config import Config, api_billing_allowed
from .deviceport import INBOUND, DevicePort
from .health import Health
from .link import GodotLink, load_token
from .memory import MemoryStore
from .skills import SkillRegistry
from .skills.builtin import ClockSkill, MemorySkill, StatusSkill
from .skills.music import MusicSkill, PearDesktopPlayer
from .voice.audio import AudioUnavailable, SoundDeviceAudio, StreamingAudio
from .voice.stt import STTUnavailable, WhisperSTT
from .voice.tts import TTSUnavailable, make_tts


def make_agent(cfg: Config, registry: SkillRegistry, memory: MemoryStore, home: Path, transport=None, budget=None):
    """Text brain for the user's chosen connection (ai.connection); None when AI is off."""
    if cfg.ai.provider != "openai":
        return None
    if cfg.ai.connection in ("chatgpt", "combined"):
        from .chatgpt_auth import ChatGPTAuth
        from .chatgpt_brain import ChatGPTBrain
        return ChatGPTBrain(cfg.ai, registry, memory, ChatGPTAuth(home, transport=transport), cfg.assistant_name,
                            transport=transport, budget=budget)
    return AgentBrain(cfg.ai, registry, memory, cfg.assistant_name, budget=budget)


class Core:
    def __init__(self, cfg: Config, home: Path, *, audio=None, player=None, stt=None, tts=None,
                 live_factory=None, console_logs: bool = True, use_real_audio: bool = True, ai_transport=None,
                 wake_detector=None, wake_audio=None) -> None:
        self.cfg, self.home = cfg, home
        self._audio, self._player, self._stt, self._tts = audio, player, stt, tts
        self._ai_transport = ai_transport          # tests inject a mock HTTP transport for the ChatGPT route
        self._wake_detector, self._wake_audio = wake_detector, wake_audio   # tests inject fakes
        self.wake = None                           # WakeListener when the wake word is active
        self._live_factory = live_factory          # tests inject a fake live session
        self.console_logs, self.use_real_audio = console_logs, use_real_audio
        self.stop_event = asyncio.Event()
        self.live = None
        self.listening = False
        self._busy: asyncio.Task | None = None
        self._confirm: dict[str, tuple[asyncio.Future, str]] = {}   # request id -> (answer, face client id)
        self._bg: list[asyncio.Task] = []
        self._tasks: set[asyncio.Task] = set()     # short fire-and-forget tasks (error notices); awaited on stop
        self.stopping = False
        self._greeted = False
        self.voice_mode = "off"

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        c = self.cfg
        self.log_path = logs.setup(self.home / "logs", c.log.level, c.log.max_bytes, c.log.backups, self.console_logs,
                                  c.log.console_level, c.log.modules, c.log.hide)
        logs.new_cid()
        logs.info("core", "CORE_STARTING", f"JARVES Core {__version__} starting", home=str(self.home))
        self.health = Health(self.home / "health.json")
        self.memory = MemoryStore(self.home / "memory.db")

        self.registry = SkillRegistry()
        self.registry.confirm = self.ask_confirm
        self.registry.register(ClockSkill())
        self.registry.register(MemorySkill(self.memory))
        self.registry.register(StatusSkill(self.snapshot))
        if c.music.provider != "none" or self._player is not None:
            player = self._player or PearDesktopPlayer(c.music.pear_url, self.home / "pear_client_id", c.music.timeout_s)
            self.music = MusicSkill(player, c.music.max_volume)
            self.registry.register(self.music)
        else:
            self.music = None
            self.health.set("music", "disabled", "music.provider = none")

        self.budget = Budget(self.memory, c.ai)      # ONE shared budget: in-flight counts span brain, TTS and voice
        self.agent = make_agent(c, self.registry, self.memory, self.home, self._ai_transport, self.budget)
        self.brain = Brain(self.registry, self.agent)
        self._check_ai()

        self.link = GodotLink(c.link.host, c.link.port, load_token(self.home / "link_token"), self.health,
                              self.on_message, self._face_connected)
        await self.link.start()

        audio = self._audio
        if audio is None and self.use_real_audio and c.voice.mode != "off":
            audio = SoundDeviceAudio(c.voice.input_device, c.voice.output_device, c.voice.record_max_s)
        self.dev = DevicePort(audio, self.link, self.health)
        self.dev.check_audio()
        self.health.set("mic", "ok", "software mute off")

        self.stt = self._stt or WhisperSTT(c.voice.stt_model, c.voice.stt_fallback_model, c.voice.stt_compute,
                                           c.voice.language)
        if self._stt is None and self.use_real_audio and c.voice.mode != "off":
            # Load the offline STT model BEFORE any Windows speech (WinRT) call: QA on 18 Oct found that loading
            # CTranslate2 in a worker thread after WinRT was used crashes the process (access violation).
            # Preloading also makes the first spoken answer faster.
            try:
                await self.stt.ensure_loaded()
                self.health.set("speech_in", "ok", f"offline STT {self.stt.loaded_name}")
            except (STTUnavailable, ImportError) as e:
                self.health.set("speech_in", "unavailable", getattr(e, "message", str(e)),
                                getattr(e, "action", "uv sync --extra voice"))

        self.tts = self._tts or make_tts(c, self.budget)
        ok, quality, detail = self.tts.check()
        self.health.set("speech_out", ("ok" if quality == "cloud" else "degraded") if ok else "unavailable",
                        detail, "" if quality == "cloud" else
                        ("ChatGPT plan has no voice; use ai.connection = \"combined\" + OPENAI_API_KEY for the premium voice"
                         if c.ai.connection == "chatgpt" else "run `jarvis key` for the high-quality voice"))
        self._select_voice_mode()

        self._bg.append(asyncio.create_task(self._health_loop()))
        self._setup_wake()
        self.health.mark("ready")
        logs.info("core", "CORE_READY", f"Core ready (voice: {self.voice_mode})", log=str(self.log_path))

    async def stop(self, grace_s: float = 3.0) -> None:
        """Orderly shutdown (CR-11): refuse new work, end voice, deny pending confirmations, let an in-flight
        request finish briefly (tool side effects are not cut mid-way), cancel and AWAIT every task, then close IO."""
        if self.stopping:
            return
        self.stopping = True
        self.health.mark("stopping")
        logs.info("core", "CORE_STOPPING", "Core stopping")
        for fut, _ in list(self._confirm.values()):
            if not fut.done():
                fut.set_result(False)              # pending confirmation = denied, never approved by shutdown
        self._confirm.clear()
        if self.live is not None:
            live, self.live = self.live, None
            try:
                await asyncio.wait_for(live.stop(), 5)
            except Exception:
                logs.error("voice", "LIVE_STOP_FAILED", "live voice did not stop cleanly", exc=True)
        busy = self._busy
        if busy is not None and not busy.done():
            done, _ = await asyncio.wait({busy}, timeout=grace_s)
            if not done:
                busy.cancel()
                logs.warn("core", "CORE_TASK_CANCELLED", "in-flight request cancelled by shutdown")
            await asyncio.gather(busy, return_exceptions=True)
        tasks = [t for t in self._bg + list(self._tasks) if not t.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.registry.drain()                 # shielded side effects finish before the store closes
        await self.link.stop()
        if self.music is not None and hasattr(self.music.player, "close"):
            await self.music.player.close()
        self.memory.close()
        self.health.mark("stopped")
        logs.info("core", "CORE_STOPPED", "Core stopped cleanly")

    async def run(self) -> None:
        await self.start()
        try:
            await self.stop_event.wait()
        finally:
            await self.stop()

    # ------------------------------------------------------------------ health
    def snapshot(self) -> dict:
        s = self.health.snapshot(self.home)
        s.update(version=__version__, voice_mode=self.voice_mode, face_state=self.dev.face_state,
                 mic_muted=self.dev.mic_muted, live_session=self.live is not None,
                 ai_connection=self.cfg.ai.connection, billing=billing_routes(self.cfg.ai, self.cfg.voice),
                 ai_requests_today=self.memory.usage_today("ai_requests"),
                 voice_minutes_today=round(self.memory.usage_today("voice_seconds") / 60, 1))
        return s

    def _check_ai(self) -> None:
        if self.agent is None:
            self.health.set("ai", "disabled", "ai.provider = offline; built-in commands only")
            return
        ok, code, action = self.agent.available()
        if ok and hasattr(self.agent, "describe"):
            self.health.set("ai", "ok", self.agent.describe())
        elif ok:
            self.health.set("ai", "ok", f"OpenAI API key, {self.cfg.ai.text_model} (web search {'on' if self.cfg.ai.web_search else 'off'})")
        else:
            self.health.set("ai", "unavailable", f"{code}: {action}", action)

    def _select_voice_mode(self) -> None:
        m = self.cfg.voice.mode
        if m == "off":
            self.voice_mode = "off"
            self.health.set("voice", "disabled", "voice.mode = off")
        elif m in ("live", "realtime") and api_billing_allowed(self.cfg.ai):
            self.voice_mode = m
            model = self.cfg.voice.live_model if m == "live" else self.cfg.voice.realtime_model
            billed = " — voice billed to your API key" if self.cfg.ai.connection == "combined" else ""
            self.health.set("voice", "ok", f"{model} (tap talk to start a conversation){billed}")
        else:
            self.voice_mode = "push_to_talk"
            plan_only = self.cfg.ai.connection == "chatgpt"
            why = "" if m == "push_to_talk" else ("ChatGPT plan has no live voice: " if plan_only else "no OPENAI_API_KEY: ")
            act = ("for live voice choose ai.connection = \"combined\" and set OPENAI_API_KEY" if plan_only
                   else "run `jarvis key` for live conversation")
            self.health.set("voice", "degraded" if why else "ok", f"{why}offline push-to-talk ({self.cfg.voice.stt_model})",
                            act if why else "")

    def _voice_budget_left_s(self) -> float:
        return self.budget.remaining("voice_seconds")

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(20)
            try:
                if self.music is not None:
                    st, d = await self.music.health()
                    self.health.set("music", st, d, "" if st == "ok" else "start Pear Desktop with the API Server plugin")
                if not self.listening and self.live is None:
                    self.dev.check_audio()
                self._check_ai()
                await self.link.broadcast("status", self.snapshot())
            except Exception:
                logs.error("core", "HEALTH_LOOP_ERROR", "health check failed", exc=True)

    # ------------------------------------------------------------------ face / UI messages
    async def _face_connected(self) -> None:
        await self.dev.face_set_state(self.dev.face_state)
        await self.link.send_face("status", self.snapshot())
        if self.cfg.voice.greeting and not self._greeted:
            self._greeted = True
            self._run_busy(self.greet())

    def how_to_talk(self) -> str:
        if self.wake is not None:
            return "תגיד \"היי ג'ארבס\" ואז דבר, או כתוב לי."
        if self.voice_mode in ("live", "realtime"):
            return "לחץ על Talk כדי להתחיל שיחה, או כתוב לי."
        if self.voice_mode == "push_to_talk":
            return "החזק רווח ודבר, או כתוב לי."
        return "כתוב לי בתיבה למטה."

    async def greet(self) -> None:
        text = f"שלום! אני {self.cfg.assistant_name}. {self.how_to_talk()}"
        await self.link.send_face("assistant.text", {"text": text, "source": "core", "ok": True, "code": "GREETING"})
        await self.speak(text)
        await self.set_face("happy")
        await asyncio.sleep(1.0)
        await self.set_face("idle")

    async def set_face(self, state: str) -> None:
        await self.dev.face_set_state(state)

    async def on_message(self, msg: dict, client) -> None:
        """Dispatch an authenticated, role-checked message (link.py enforces the per-role allow-list)."""
        t, p, ws = msg["type"], msg.get("payload") or {}, client
        logs.verbose("link", "MSG_IN", t, role=client.role)
        if self.stopping and t != "status.request":
            await self.link.reply(ws, "error", {"code": "CORE_STOPPING", "message": "JARVES is shutting down"})
            return
        if t in ("talk.down",) or (t == "button.down" and p.get("id") == "action"):
            await self.talk_down()
        elif t in ("talk.up",) or (t == "button.up" and p.get("id") == "action"):
            await self.talk_up()
        elif t == "user.text":
            text = str(p.get("text", "")).strip()[:1000]
            if text:
                self._run_busy(self.handle_utterance(text, typed=True))
        elif t in ("mic.set_mute", "mic.mute_changed"):
            await self.set_muted(bool(p.get("muted")))
        elif t == "status.request":
            await self.link.reply(ws, "status", self.snapshot())
        elif t == "confirm.reply":
            req = self._confirm.get(str(p.get("id")))
            if req is None or req[1] != client.id:      # only the face that was asked may answer
                logs.warn("core", "CONFIRM_SPOOFED", "confirmation reply from a client that was not asked",
                          role=client.role)
                return
            self._confirm.pop(str(p.get("id")), None)
            if not req[0].done():
                req[0].set_result(p.get("approved") is True)
        elif t == "debug.enable":
            logs.enable_debug(min(int(p.get("minutes", self.cfg.log.debug_minutes)), self.cfg.log.debug_minutes))
            logs.info("core", "DEBUG_ENABLED", "temporary DEBUG logging enabled")
        elif t == "core.shutdown":
            logs.info("core", "SHUTDOWN_REQUESTED", "shutdown requested by an authenticated client")
            self.stop_event.set()
        elif t in INBOUND:
            logs.info("device", "DEVICE_EVENT", f"device event {t}", **logs.safe_fields(p, allow=("id", "muted", "state")))
        else:
            await self.link.reply(ws, "error", {"code": "LINK_UNKNOWN_TYPE", "message": f"unknown message type {t}"})

    def _run_busy(self, coro) -> None:
        if self.stopping:
            coro.close()
            return
        if self._busy and not self._busy.done():
            self._busy.cancel()                # a new request preempts thinking; tool calls are shielded in the registry
        self._busy = asyncio.create_task(coro)
        self._busy.add_done_callback(self._busy_done)

    def _spawn(self, coro) -> None:
        if self.stopping:
            coro.close()
            return
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def _busy_done(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is None:
            return
        e = task.exception()
        logs.log("core", "CORE_TASK_CRASH", f"request failed: {type(e).__name__}: {str(e)[:200]}", 40)
        self.health.set("core_tasks", "degraded", f"last request crashed ({type(e).__name__})", "see logs; try again")
        self._spawn(self.notify_error("CORE_INTERNAL", "internal error", "try again"))

    async def set_muted(self, muted: bool) -> None:
        self.dev.set_mic_muted(muted)
        if self.wake is not None:
            self.wake.mute() if muted else self.wake.unmute()
        if muted and self.live is not None:
            await self.live.stop()
            self.live = None
        if muted and self.listening:
            self.listening = False
            await self.set_face("idle")
        await self.link.broadcast("status", self.snapshot())

    async def notify_error(self, code: str, message: str, action: str = "") -> None:
        await self.link.send_face("error", {"code": code, "message": message, "action": action})
        await self.set_face("error")
        await asyncio.sleep(1.5)
        await self.set_face("idle")

    # ------------------------------------------------------------------ talking
    async def talk_down(self) -> None:
        if self.dev.mic_muted:
            await self.notify_error("MIC_MUTED", "the microphone is muted", "unmute to talk")
            return
        if self.voice_mode in ("live", "realtime"):
            if self.live is not None:          # tap again = end the conversation
                live, self.live = self.live, None
                await live.stop()
                return
            await self._start_live()
            return
        if self.voice_mode != "push_to_talk":
            return
        self.dev.speaker_stop()                # barge-in: talking interrupts JARVES
        if self._busy and not self._busy.done():
            self._busy.cancel()
        try:
            self.dev.mic_start()
        except AudioUnavailable as e:
            self.health.set("audio", "unavailable", e.message, e.action)
            await self.notify_error(e.code, e.message, e.action)
            return
        self.listening = True
        await self.set_face("listening")

    async def talk_up(self) -> None:
        if self.voice_mode != "push_to_talk" or not self.listening:
            return
        self.listening = False
        audio = self.dev.mic_stop()
        self._run_busy(self._transcribe_and_answer(audio))

    async def _transcribe_and_answer(self, audio) -> None:
        logs.new_cid()
        await self.set_face("thinking")
        try:
            text = await self.stt.transcribe(audio)
            self.health.set("speech_in", "ok", f"offline STT {self.stt.loaded_name}")
        except STTUnavailable as e:
            self.health.set("speech_in", "unavailable", e.message, e.action)
            await self.notify_error(e.code, e.message, e.action)
            return
        if not text:
            peak = float(abs(audio).max()) if getattr(audio, "size", 0) else 0.0
            if peak < 0.003:     # true silence: the mic delivers nothing (privacy setting, wrong/disabled device)
                self.health.set("mic", "degraded", "microphone delivers silence",
                                "Windows: Settings > Privacy & security > Microphone (allow desktop apps); check the input device")
                await self.notify_error("MIC_SILENT", "לא מגיע קול מהמיקרופון",
                                        "בדוק הרשאות מיקרופון ב-Windows ואת התקן הקלט")
                return
            await self.link.send_face("assistant.text", {"text": "לא הבנתי. נסה שוב, קצת יותר קרוב.", "source": "core"})
            await self.set_face("idle")
            return
        await self.link.send_face("user.transcript", {"text": text})
        await self.handle_utterance(text, typed=False)

    async def handle_utterance(self, text: str, typed: bool) -> None:
        if typed:
            logs.new_cid()
        await self.set_face("thinking")
        t0 = time.monotonic()
        reply = await self.brain.respond(text)
        logs.info("core", "REPLY", f"reply via {reply.source}", tool=reply.tool, ok=reply.ok, result=reply.code,
                  ms=round((time.monotonic() - t0) * 1000), text=reply.text)
        await self.link.send_face("assistant.text", {"text": reply.text, "source": reply.source, "ok": reply.ok,
                                                     "code": reply.code})
        if self.live is None:
            await self.speak(reply.text)
        await self.set_face("happy" if reply.ok and reply.tool else "idle")
        if reply.ok and reply.tool:
            await asyncio.sleep(1.2)
            await self.set_face("idle")

    async def speak(self, text: str) -> None:
        if not text or self.dev.audio is None:
            return
        try:
            samples, rate = await self.tts.synth(text)
        except TTSUnavailable as e:
            self.health.set("speech_out", "unavailable", e.message, e.action)
            return
        await self.set_face("speaking")
        try:
            await self.dev.speaker_play(samples, rate)
        except AudioUnavailable as e:
            self.health.set("audio", "unavailable", e.message, e.action)

    async def _start_live(self) -> None:
        if self._voice_budget_left_s() <= 0:
            await self.notify_error("VOICE_BUDGET", "daily live-voice limit reached",
                                    "raise ai.daily_realtime_minutes or use push-to-talk")
            return
        v = self.cfg.voice
        audio_io = StreamingAudio(v.input_device, v.output_device) if self.use_real_audio else None
        max_s = min(v.session_max_s, self._voice_budget_left_s())

        def usage(seconds: float) -> None:
            self.budget.record("voice_seconds", int(round(seconds)))

        async def on_transcript(role: str, text: str) -> None:
            await self.link.send_face("user.transcript" if role == "user" else "assistant.text",
                                      {"text": text, "source": self.voice_mode})

        async def on_end(reason: str) -> None:
            ended[0] = True
            if self.live is live_ref[0]:
                self.live = None                   # any end (user, remote close, error, budget) frees the slot
            logs.info("voice", "LIVE_ENDED", f"live conversation ended ({reason})")
            if reason.startswith("error:") or reason == "remote_closed":
                self.health.set("voice", "degraded", f"live conversation ended: {reason}", "tap Talk to start again")
                self._spawn(self.notify_error("LIVE_ENDED", "השיחה החיה נותקה", "לחץ Talk כדי להתחיל שוב"))
            elif reason == "budget":
                self._spawn(self.notify_error("AI_BUDGET", "הגעתי למגבלת השימוש היומית", "ai.daily_request_limit"))
            await self.link.broadcast("status", self.snapshot())

        def on_health(status: str, detail: str, action: str = "") -> None:
            self.health.set("voice", status, detail, action)

        live_ref: list = [None]
        ended = [False]
        common = dict(on_state=self.set_face, on_transcript=on_transcript, on_usage=usage)
        extra = dict(on_end=on_end, on_health=on_health, budget=self.budget)
        if self._live_factory is not None:
            live = self._live_factory(self, **common, **extra)
        elif self.voice_mode == "live":
            from .voice.live import LiveVoice
            live = LiveVoice(self.registry, self.agent.instructions, model=v.live_model, voice=v.voice,
                             backend_model=self.cfg.ai.text_model, web_search=self.cfg.ai.web_search,
                             name=self.cfg.assistant_name, half_duplex=v.half_duplex, max_session_s=max_s,
                             audio_io=audio_io, **common, **extra)
        else:
            from .voice.realtime import RealtimeVoice
            live = RealtimeVoice(v.realtime_model, self.agent.instructions, self.registry.realtime_tools(),
                                 v.language, v.voice, v.half_duplex, audio_io, max_session_s=max_s, **common, **extra)
        live_ref[0] = live
        logs.new_cid()
        try:
            await live.start()
        except BudgetExceeded as e:                 # F1: no unit can be reserved ahead -> no session at all
            await self.notify_error(e.code, "הגעתי למגבלת השימוש היומית", e.action)
            return
        except Exception as e:
            logs.error("voice", "LIVE_START_FAILED", f"live voice failed to start: {type(e).__name__}: {str(e)[:150]}")
            self.health.set("voice", "degraded", f"live voice failed: {type(e).__name__}",
                            "check OPENAI_API_KEY/internet; push-to-talk still works")
            if ended[0]:
                return                              # on_end already told the user
            await self.notify_error("LIVE_START_FAILED", "live voice could not start", "check the internet / API key")
            return
        if getattr(live, "running", True) is False:
            return                                  # ended during start (on_end already handled it)
        self.live = live

    # ------------------------------------------------------------------ "Hey Jarvis" wake word
    def _setup_wake(self) -> None:
        v = self.cfg.voice
        if not v.wake_enabled or self.voice_mode == "off" or not (self._wake_audio or self.use_real_audio):
            self.health.set("wake_word", "disabled", "wake word off (voice.wake_enabled / voice.mode)")
            return
        from .voice.wakeword import WakeListener, WakeWordUnavailable, make_detector
        try:
            det = self._wake_detector or make_detector(v.wake_word, v.wake_threshold)
            ok, msg = det.check() if hasattr(det, "check") else (True, "")
        except WakeWordUnavailable as e:
            ok, msg = False, e.message
        if not ok:
            self.health.set("wake_word", "unavailable", msg, "uv sync --extra voice; hold Space to talk meanwhile")
            return
        self.wake = WakeListener(det, self._on_wake, v.wake_cooldown_s)
        self._woke = False
        self.health.set("wake_word", "ok", f"say \"Hey Jarvis\" (detected on this device, {v.wake_word})")
        self._bg.append(asyncio.create_task(self._wake_loop()))

    async def _on_wake(self, score: float) -> None:
        self._woke = True
        logs.info("wake", "WAKE_WORD", "wake word heard", score=round(score, 2))

    async def _levels(self, chunks):
        """Pass mic chunks through, sending the face a ~5 Hz mic level for the level bar."""
        from .voice.wakeword import level_meter
        last = 0.0
        async for c in chunks:
            now = time.monotonic()
            if now - last > 0.2 and self.link.face is not None:
                last = now
                await self.link.send_face("mic.level", {"level": round(level_meter(c), 3)})
            yield c

    def _wake_should_listen(self) -> bool:
        return not (self.dev.mic_muted or self.live is not None or self.listening or self.stopping
                    or (self._busy is not None and not self._busy.done()))

    async def _wake_loop(self) -> None:
        """One local mic stream at 16 kHz: wake word -> live conversation, or capture-until-silence -> answer.
        The stream is closed (device released) while muted, during a live session and while JARVES answers."""
        from .voice.wakeword import capture_until_silence
        v = self.cfg.voice
        load = getattr(self.wake.detector, "load", None)
        if load is not None:
            await load()
        while not self.stopping:
            if not self._wake_should_listen():
                await asyncio.sleep(0.2)
                continue
            mic = self._wake_audio() if self._wake_audio else StreamingAudio(v.input_device, v.output_device, rate=16000)
            chunks = self._levels(mic.mic_chunks(16000, 2560))      # 80 ms chunks
            woke = False
            try:
                self.wake.resume()
                async for chunk in chunks:
                    if not self._wake_should_listen():
                        break
                    self._woke = False
                    await self.wake.process(chunk)
                    if self.wake.stats.frames % 25 == 0:
                        logs.verbose("wake", "WAKE_SCORE", "wake word score", score=round(self.wake.stats.last_score, 3))
                    if self._woke:
                        woke = True
                        break
                if woke and self.voice_mode == "push_to_talk":
                    logs.new_cid()
                    self.listening = True
                    self.dev.speaker_stop()
                    await self.set_face("listening")
                    await self.link.send_face("assistant.activity", {"text": "כן? אני מקשיב..."})
                    cap = await capture_until_silence(chunks, max_s=v.record_max_s)
                    self.listening = False
                    if cap.speech_ms == 0 and cap.too_quiet:
                        self.wake.note_false_trigger()
                    self._run_busy(self._transcribe_and_answer(cap.audio))
            except AudioUnavailable as e:
                self.health.set("audio", "unavailable", e.message, e.action)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception:
                logs.error("wake", "WAKE_LOOP_ERROR", "wake word loop failed; retrying", exc=True)
                self.health.set("wake_word", "degraded", "wake word loop crashed; retrying", "see logs")
                await asyncio.sleep(2)
            finally:
                self.listening = False
                self.wake.pause()
                await chunks.aclose()
                mic.close()
            if woke and self.voice_mode in ("live", "realtime"):
                await self._start_live()

    # ------------------------------------------------------------------ confirmation for side effects
    async def ask_confirm(self, prompt: str, timeout: float = 20) -> bool:
        face_id = self.link.face_client_id()
        if face_id is None or self.stopping:
            return False
        cid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._confirm[cid] = (fut, face_id)
        await self.link.send_face("confirm.request", {"id": cid, "prompt": prompt})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            self._confirm.pop(cid, None)
