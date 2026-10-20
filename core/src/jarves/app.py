"""JARVES Core: lifecycle, state, Godot face link, DevicePort, brain, voice modes, Skills, health."""
from __future__ import annotations

import asyncio
import time
import hashlib
import json
from contextlib import aclosing
from pathlib import Path

from . import __version__, logs, store
from .brain import AgentBrain, Brain, billing_routes
from .budget import Budget, BudgetExceeded
from .config import Config, api_billing_allowed, openai_key_present
from .deviceport import INBOUND, DevicePort
from .health import Health
from .link import GodotLink, load_token
from .memory import MemoryStore
from .skills import SkillRegistry
from .skills.builtin import ClockSkill, MemorySkill, StatusSkill
from .skills.music import MusicSkill, PearDesktopPlayer
from .skills.settings import Settings, SettingsSkill
from . import workspace as wsp
from . import features as feat
from . import browser as browser_mod            # SW-V3 P1.2: registers the `browser` switch (default OFF)
from .curator import _SENSITIVE as SENSITIVE_TEXT, Curator, openai_extractor
from .dashboard import DashboardSkill
from .voice.audio import AudioUnavailable, SoundDeviceAudio, StreamingAudio
from .voice.speechcache import SpeechCache
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


NEAR_MISS_MIN = 0.12          # a wake score at/above this but below the threshold is logged (WAKE_NEAR_MISS)


def _mic_name(device) -> str:
    try:
        import sounddevice as sd
        return str(sd.query_devices(device, kind="input")["name"])[:60]
    except Exception:
        return "unknown"


def _speaker_name(device) -> str:
    try:
        import sounddevice as sd
        return str(sd.query_devices(device, kind="output")["name"])[:60]
    except Exception:
        return "unknown"


class _FaceApproval:
    """approvals channel: the Godot confirm dialog, bound to the face client that was asked (typed-code requests
    are not offered there: the face has no code field)."""
    name = "face"

    def __init__(self, core) -> None:
        self.core = core

    def available(self, a) -> bool:
        return not a.typed_code and getattr(self.core, "link", None) is not None \
            and self.core.link.face_client_id() is not None

    async def open(self, a) -> None:
        self.core._confirm[a.id] = self.core.link.face_client_id()
        await self.core.link.send_face("confirm.request", {"id": a.id, "prompt": a.prompt, "risk": a.risk,
                                                           "kind": a.kind})

    async def close(self, a) -> None:
        if self.core._confirm.pop(a.id, None) is not None and a.channel != "face":
            await self.core.link.send_face("confirm.cancel", {"id": a.id})   # answered elsewhere: close the dialog


class _VoiceApproval:
    """approvals channel: the live conversation is told an approval waits (JARVES asks for it once); the owner's
    yes/no comes back through Core._owner_answer. Not counted for typed-code requests."""
    name = "voice"

    def __init__(self, core) -> None:
        self.core = core
        self._live: dict[str, object] = {}

    def available(self, a) -> bool:
        live = self.core.live
        return not a.typed_code and live is not None and hasattr(live, "confirm_pending")

    async def open(self, a) -> None:
        live = self._live[a.id] = self.core.live
        try:
            await live.confirm_pending(a.prompt)
        except Exception:
            logs.warn("voice", "LIVE_CONFIRM_NOTE_FAILED", "could not tell the voice session about the confirmation")

    def close(self, a) -> None:
        live = self._live.pop(a.id, None)
        if live is not None and hasattr(live, "confirm_done"):
            live.confirm_done()


class Core:
    def __init__(self, cfg: Config, home: Path, *, audio=None, player=None, stt=None, tts=None,
                 live_factory=None, console_logs: bool = True, use_real_audio: bool = True, ai_transport=None,
                 wake_detector=None, wake_audio=None, runtime_mode: str = "foreground", viewer_launcher=None,
                 tray_factory=None, autostart=None) -> None:
        self.cfg, self.home = cfg, home
        self._audio, self._player, self._stt, self._tts = audio, player, stt, tts
        self._ai_transport = ai_transport          # tests inject a mock HTTP transport for the ChatGPT route
        self._wake_detector, self._wake_audio = wake_detector, wake_audio   # tests inject fakes
        # always-on runtime (runtime/): "foreground" (terminal), "desktop" (`jarvis`), "background" (no window)
        self.runtime_mode = runtime_mode
        self._runtime_fakes = dict(launcher=viewer_launcher, tray_factory=tray_factory, autostart=autostart)
        self.runtime = None
        self.wake = None                           # WakeListener when the wake word is active
        self._wake_status: dict | None = None      # last wake finding (heard speech / too quiet / near miss / woke)
        self._live_factory = live_factory          # tests inject a fake live session
        self.console_logs, self.use_real_audio = console_logs, use_real_audio
        # open_dashboard opens the browser only on a real desktop run (tests / silent runs never open windows)
        import webbrowser
        self.open_url = webbrowser.open if use_real_audio else None
        self._dashboard = None
        self.stop_event = asyncio.Event()
        self.live = None
        self.listening = False
        self._busy: asyncio.Task | None = None
        self._confirm: dict[str, str] = {}         # approval id -> the face client id that was asked
        self.approvals = None
        self._bg: list[asyncio.Task] = []
        self._speech_cache = SpeechCache(home)
        self._tasks: set[asyncio.Task] = set()     # short fire-and-forget tasks (error notices); awaited on stop
        self.stopping = False
        self._greeted = False
        self.voice_mode = "off"
        self._voice_connecting = False
        self._hold_wake_until = 0.0     # connected-on-open mode: wake word waits until the first live session ended
        self._rollovers: list[float] = []   # times of automatic session resumes (loop guard)
        self.startup = {"phase": "starting", "ready": False, "detail": "Loading JARVES..."}
        self._startup_t0 = time.monotonic()
        self._startup_signature = hashlib.sha256(json.dumps([
            "live-greeting-v1", cfg.voice.activation, cfg.voice.mode, cfg.voice.live_model, cfg.voice.voice,
            cfg.voice.greeting]).encode()).hexdigest()
        self._startup_estimate = None
        try:
            timing = json.loads((home / "startup_timing.json").read_text(encoding="utf-8"))
            if isinstance(timing, dict) and timing.get("signature") == self._startup_signature and 0 < float(timing["seconds"]) < 300:
                self._startup_estimate = float(timing["seconds"])
        except (OSError, ValueError, TypeError, KeyError):
            pass

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        started_at = time.monotonic()
        c = self.cfg
        self.log_path = logs.setup(self.home / "logs", c.log.level, c.log.max_bytes, c.log.backups, self.console_logs,
                                  c.log.console_level, c.log.modules, c.log.hide, privacy=c.log.privacy)
        from . import backup, usage                     # SW-V2 M5: checkpoint BEFORE the stores open/migrate
        backup.startup_checkpoint(self.home)
        self.history = store.attach(self.home, c)       # conversation history + self-diagnosis (log sink)
        logs.new_cid()
        logs.info("core", "CORE_STARTING", f"JARVES Core {__version__} starting", home=str(self.home))
        self.health = Health(self.home / "health.json")
        self.memory = MemoryStore(self.home / "memory.db")
        # SW-V2 WP-I feature switches: ONE source of truth (features.json over the legacy config.toml keys); the
        # loaded cfg mirrors them so code reading cfg.voice.greeting / cfg.ai.web_search follows the switches.
        self.features = feat.attach(feat.Features(c, self.home, components=self._health_components,
                                                  skills=lambda: set(self.registry.skills)))
        self.features.import_legacy(feat.legacy_settings(self.memory))
        self.features.apply_to_config(c)
        self.features.listeners.append(self._feature_changed)
        self._warm_probes()                              # P0.3: Pear's localhost probe overlaps the rest of start()
        # SW-V2 WP-M4 identity: user-chosen names, wake model and voice over config.toml (identity.json)
        from .identity import Identity, make_skill as make_identity_skill
        self.identity = Identity(self.home, c, self.memory)
        self.identity.apply_to_config(c)

        # V3 approvals: ONE service behind every confirmation (popup / tray / face / dashboard / voice, audit log)
        from . import approvals as appr
        self.approvals = appr.attach(appr.ApprovalService(self.home))
        # the native popup only on a real run (tests and silent runs never open windows)
        self.approvals.channels += [appr.PopupChannel(self.approvals, enabled=self.use_real_audio),
                                    _FaceApproval(self), _VoiceApproval(self)]
        self.registry = SkillRegistry()
        self.registry.confirm = self.ask_confirm
        self.registry.gate = self.features.gate          # inactive features: tools not offered, calls refused
        self.registry.register(ClockSkill())
        self.registry.register(MemorySkill(self.memory))
        from .skills.local_contacts import LocalContactsSkill
        self.registry.register(LocalContactsSkill(self.memory))
        self.registry.allowed_actions.add("contact_remember")
        self.registry.register(StatusSkill(self.snapshot))
        self.registry.register(feat.make_skill(self.features, self.registry))
        self.registry.allowed_actions |= {"feature_set"}   # policy per risk lives in the tool (features.py)
        usage.attach(self.home)                          # M5: usage meters ("כמה נשאר לי?") + daily local backup
        self.registry.register(usage.make_skill(self.home, c))
        self._backup_auto = backup.start_auto(self.home, self.health)
        if c.music.provider != "none" or self.features.enabled("music") or self._player is not None:
            player = self._player or PearDesktopPlayer(c.music.pear_url, self.home / "pear_client_id", c.music.timeout_s)
            self.music = MusicSkill(player, c.music.max_volume, trim_queue=c.music.trim_queue,
                                    volume_step=c.music.volume_step)
            self.registry.register(self.music)
        else:
            self.music = None
            self.health.set("music", "disabled", "music switched off (jarvis features on music)")
        # WP-D workspace/memory: persona files, self-settings Skill, background memory curator
        self.workspace = wsp.Workspace(self.home / "workspace")
        self.settings = Settings(self.memory, {"voice.voice": c.voice.voice})
        self.settings.features = self.features           # curator.* settings are feature switches now
        self.registry.register(SettingsSkill(self.workspace, self.settings, self.registry, self._on_voice_change))
        self.registry.register(make_identity_skill(self.identity))     # "your name is X" (confirmed read-back)
        # WP-H: the dashboard on request ("תפתח את לוח הבקרה" / face button), inside this process
        self.registry.allowed_actions.add("open_dashboard")
        self.registry.register(DashboardSkill(self.open_dashboard))
        from .skills.google import register_google      # WP-G: only when the user connected Google (no network)
        self.google = register_google(self.registry, self.home, self.health)
        from .homeassistant import register_home        # V3 P1 channels: tools stay hidden until their switch is on
        from .telegram_channel import register_telegram
        from .whatsapp import register_whatsapp
        register_telegram(self.registry, self.home, self.health)
        from .instagram_channel import register_instagram
        register_instagram(self.registry, self.home, self.health)
        register_whatsapp(self.registry, self.home)
        register_home(self.registry, self.home, self.health)
        self.workspace.listeners.append(lambda name: self._spawn(wsp.note_live(self.live, self.workspace, name)))

        self.budget = Budget(self.memory, c.ai)      # ONE shared budget: in-flight counts span brain, TTS and voice
        self.curator = Curator(self.memory, self.workspace, enabled=lambda k: self.features.active(feat.SETTING_FEATURES[k])
                               if k in feat.SETTING_FEATURES else self.settings.get(k) == "on",
                               llm=openai_extractor(c.ai.text_model, self.budget) if api_billing_allowed(c.ai) else None)
        self.agent = make_agent(c, self.registry, self.memory, self.home, self._ai_transport, self.budget)
        if self.agent is not None:
            wsp.augment_instructions(self.agent, self.workspace)
        self.brain = Brain(self.registry, self.agent)
        if self.agent is not None:                      # dashboard: what he was given in each typed conversation
            from . import context_trace
            context_trace.attach_brain(self.agent, home=self.home, cfg=c, memory=self.memory, registry=self.registry,
                                       feats=self.features)
        # Background tasks (SW-V2 WP-C, tasks.py): research runs while the conversation goes on
        from .tasks import TaskSkill, make_retry_lookup, make_task_manager
        self.tasks = make_task_manager(c, self.agent, self.budget, self.home, on_update=self._tasks_changed,
                                       on_finished=self._task_finished)
        self._announcing: set[str] = set()
        if self.tasks is not None:
            self.registry.allowed_actions |= {"task_start", "task_cancel"}
            self.registry.register(TaskSkill(self.tasks))
        from .operations import attach as ops_attach    # WP-M3: scheduler, reports, notes, files, alerts, autonomy
        self.ops = ops_attach(self)
        from . import guides                            # WP-L: help Skill, setup hints, briefing, suggestions
        self.guides = guides.attach(self)
        self._write_tools_md()                          # after every Skill is registered
        self._retry_lookup = make_retry_lookup(c, self.budget)
        self._check_ai()

        self.link = GodotLink(c.link.host, c.link.port, load_token(self.home / "link_token"), self.health,
                              self.on_message, self._face_connected)
        from .camera import wire as camera_wire              # WP-J camera: switches default OFF (camera/wire.py)
        self.camera = camera_wire.attach(self)
        self.browser = browser_mod.attach(self)              # Browser Skill: allow-list control, read-only + ask
        from . import appearance                             # WP-M2: face / 2D / 3D avatar / hidden (appearance.py)
        self.appearance = appearance.attach(self)
        from .skills import generated                        # WP-M2: approved generated Skills (skills/generated.py)
        self.generated_skills = generated.attach(self)

        audio = self._audio
        if audio is None and self.use_real_audio and c.voice.mode != "off":
            audio = SoundDeviceAudio(c.voice.input_device, c.voice.output_device, c.voice.record_max_s)
        self.dev = DevicePort(audio, self.link, self.health)
        self.dev.check_audio()
        self.health.set("mic", "ok", "software mute off")
        from . import runtime as runtime_mod            # M1: display/tray/autostart/resume; restores a saved mute
        self.runtime = runtime_mod.attach(self, mode=self.runtime_mode, **self._runtime_fakes)
        from . import profiles                          # Issue #17: profile_list / profile_switch (screen approval)
        profiles.attach(self)
        self.approvals.channels.append(appr.TrayChannel(self.runtime))   # balloon -> popup / dashboard
        from .skills.helper import register_helper       # V3: shell / install helper, every action approved first
        register_helper(self.registry, self.home, self.health)
        self._write_tools_md()                          # again: camera + display + profile + helper Skills registered after the first

        self.stt = self._stt or WhisperSTT(c.voice.stt_model, c.voice.stt_fallback_model, c.voice.stt_compute,
                                           c.voice.language)
        self._select_voice_mode()
        if self._stt is None and self.use_real_audio and self.voice_mode == "push_to_talk":
            # Load the offline STT model BEFORE any Windows speech (WinRT) call: QA on 18 Oct found that loading
            # CTranslate2 in a worker thread after WinRT was used crashes the process (access violation).
            # Preloading also makes the first spoken answer faster.
            try:
                await self.stt.ensure_loaded()
                self.health.set("speech_in", "ok", f"offline STT {self.stt.loaded_name}")
            except (STTUnavailable, ImportError) as e:
                self.health.set("speech_in", "unavailable", getattr(e, "message", str(e)),
                                getattr(e, "action", "uv sync --extra voice"))
        elif self.voice_mode in ("live", "realtime"):
            self.health.set("speech_in", "ok", "cloud voice; offline speech model not loaded")

        self.tts = self._tts or make_tts(c, self.budget)
        ok, quality, detail = self.tts.check()
        self.health.set("speech_out", ("ok" if quality == "cloud" else "degraded") if ok else "unavailable",
                        detail, "" if quality == "cloud" else
                        ("ChatGPT plan has no voice; use ai.connection = \"combined\" + OPENAI_API_KEY for the premium voice"
                         if c.ai.connection == "chatgpt" else "run `jarvis key` for the high-quality voice"))

        # Accept the room/face connection only after audio and speech are initialized.
        await self.link.start()
        self._bg.append(asyncio.create_task(self._health_loop()))
        if self.use_real_audio and openai_key_present() and self.cfg.ai.provider == "openai":
            self._bg.append(asyncio.create_task(asyncio.to_thread(self._prewarm)))
        self._setup_wake()
        self._log_summary()
        self.health.mark("ready")
        logs.info("core", "CORE_READY", f"Core ready (voice: {self.voice_mode})", log=str(self.log_path),
                  ms=round((time.monotonic() - started_at) * 1000))
        self._startup_stage("ready" if self.voice_mode == "off" else "core_ready",
                            "Core ready (voice off)" if self.voice_mode == "off" else "Core loaded. Preparing voice...")

    async def stop(self, grace_s: float = 3.0) -> None:
        """Orderly shutdown (CR-11): refuse new work, end voice, deny pending confirmations, let an in-flight
        request finish briefly (tool side effects are not cut mid-way), cancel and AWAIT every task, then close IO."""
        if self.stopping:
            return
        self.stopping = True
        self.dev.speaker_stop()
        self.health.mark("stopping")
        logs.info("core", "CORE_STOPPING", "Core stopping")
        if getattr(self, "history", None) is not None:
            self.history.ai_reviews = False         # P0.4 Q06: no AI review starts during shutdown (rules still run)
        if getattr(self, "approvals", None) is not None:
            self.approvals.close()                 # pending approvals = denied, never approved by shutdown
            from . import approvals as appr
            appr.detach(self.approvals)
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
        if getattr(self, "tasks", None) is not None:
            await self.tasks.shutdown()             # running background tasks end as "cancelled" (stored)
        if getattr(self, "ops", None) is not None:
            await self.ops.stop()                   # running jobs stop; the next start resumes or marks them
        await self.registry.drain()                 # shielded side effects finish before the store closes
        if getattr(self, "camera", None) is not None:
            await self.camera.stop()                # sensor closed, indicator off, faces.db closed
        if getattr(self, "browser", None) is not None:
            await self.browser.close("core stopping")   # headless browser + its private session gone
        self.curator.cancel()
        try:                                        # what this run learned is kept (rules only: no network now)
            self.curator.llm = None
            await asyncio.wait_for(self.curator.run("shutdown"), 3)
        except Exception:
            logs.warn("memory", "CURATOR_SHUTDOWN_FAILED", "memory curator did not finish at shutdown")
        if self.runtime is not None:
            await self.runtime.stop()               # closes the window it opened; tray/dashboard off; state saved
        await self.link.stop()
        if self._dashboard is not None:              # WP-H: the in-Core dashboard shares memory.db
            await asyncio.to_thread(self._dashboard.stop)
        if self.music is not None:
            await self.music.close()                # cancels a background queue trim, then closes the client
        self.memory.close()
        self.budget.close()          # QA 19 Oct (suite_voice): its own SQLite handle kept memory.db locked after stop
        self.health.mark("stopped")
        logs.info("core", "CORE_STOPPED", "Core stopped cleanly")
        if getattr(self, "_backup_auto", None) is not None:
            self._backup_auto.stop.set()                # M5 automatic backup thread
        store.detach(getattr(self, "history", None))
        from . import context_trace
        context_trace.close_home(self.home)             # its own store.db handle (conversation context)

    async def run(self) -> None:
        await self.start()
        try:
            await self.stop_event.wait()
        finally:
            await self.stop()

    # ------------------------------------------------------------------ health
    def snapshot(self) -> dict:
        s = self.health.snapshot(self.home)
        startup = dict(self.startup)
        startup["elapsed_s"] = round(time.monotonic() - self._startup_t0, 1)
        startup["eta_s"] = (max(0, round(self._startup_estimate - startup["elapsed_s"]))
                            if self._startup_estimate is not None else None)
        from . import profiles
        s.update(version=__version__, voice_mode=self.voice_mode, voice_activation=self.cfg.voice.activation,
                 profile=profiles.info(self.home),                     # face / dashboard: name + TEST banner
                 startup=startup,
                 face_state=self.dev.face_state,
                 mic_muted=self.dev.mic_muted, live_session=self.live is not None,
                 runtime=self.runtime.view() if self.runtime is not None else None,
                 privacy=self.privacy_state.state() if getattr(self, "privacy_state", None) else None,
                 voice_connecting=self._voice_connecting,
                 wake_listening=getattr(self, "_wake_mic_active", False),
                 wake_status=getattr(self, "_wake_status", None),
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
                self.features.poll()            # a switch flipped on the dashboard / CLI reaches the Core here
                self._check_ai()
                await self.link.broadcast("status", self.snapshot())
            except Exception:
                logs.error("core", "HEALTH_LOOP_ERROR", "health check failed", exc=True)

    # ------------------------------------------------------------------ face / UI messages
    async def _face_connected(self) -> None:
        if self.startup["ready"] and self.live is not None and hasattr(self.live, "present"):
            self._spawn(self._present_after_delay(self.live))
        if self.startup["phase"] == "ready":
            self._startup_stage("running", "Ready")
            seconds = round(time.monotonic() - self._startup_t0, 2)
            try:
                (self.home / "startup_timing.json").write_text(json.dumps({
                    "signature": self._startup_signature, "seconds": seconds}), encoding="utf-8")
            except OSError:
                logs.warn("core", "STARTUP_TIMING_CACHE_FAILED", "could not save startup duration estimate")
        await self.dev.face_set_state(self.dev.face_state)
        await self.link.send_face("status", self.snapshot())
        if self.cfg.voice.activation == "conversation" and not self._greeted:
            self._greeted = True
            self._run_busy(self.greet())
        elif self.cfg.voice.activation == "wake_word" and self.cfg.voice.greeting and not self._greeted:
            self._greeted = True
            logs.info("core", "GREETING", "saying hello (once per start)", speech="cloud" if (self.cfg.ai.provider == "openai" and self.cfg.ai.connection != "chatgpt") else "local")
            self._run_busy(self.greet())      # busy => the wake mic is closed while JARVES talks (no self-trigger)

    def how_to_talk(self) -> str:
        if self.cfg.voice.activation == "conversation":
            return "השיחה נפתחת אוטומטית. אפשר לדבר בלי מילת הפעלה."
        if self.wake is not None:
            return "תגיד \"היי ג'ארוויס\", המתן לחיבור ואז דבר."
        if self.voice_mode in ("live", "realtime"):
            return "הפעל זיהוי מילת הפעלה בהגדרות הקול כדי לדבר."
        if self.voice_mode == "push_to_talk":
            return "הקול המקומי זמין דרך מילת ההפעלה כשהמיקרופון מוכן."
        return "הקול אינו זמין. בדוק את המצב באפשרויות."

    def hello_text(self, question: bool = False) -> str:
        """The spoken hello: always the Hebrew-written name (19 Oct: "אני JARVES" was mispronounced)."""
        return f"שלום! אני {self.cfg.assistant_name_spoken}." + (" איך אפשר לעזור?" if question else "")

    async def _present_after_delay(self, live) -> None:
        """Release the held live greeting a beat after the room is ready instead of the same instant."""
        await asyncio.sleep(self.cfg.voice.greeting_delay_s)
        if self.live is live:
            live.present()

    async def greet(self) -> None:
        hello = self.hello_text()
        text = f"{hello} {self.how_to_talk()}"
        await self.link.send_face("assistant.text", {"text": text, "source": "core", "ok": True, "code": "GREETING"})
        if self.cfg.voice.greeting:
            await asyncio.sleep(self.cfg.voice.greeting_delay_s)     # a beat after the room is ready (cache stays instant)
            await self.speak(f'{hello} תגיד היי ג׳ארוויס כדי לדבר איתי.' if self.wake is not None else hello,
                             cache=True)
        if self.cfg.voice.activation == "conversation":
            if self.voice_mode in ("live", "realtime"):
                await self._start_live()
            else:
                self.health.set("conversation", "unavailable", "automatic conversation needs live cloud voice",
                                "configure API voice, or select voice.activation = wake_word for local speech")
                await self.link.broadcast("status", self.snapshot())
            return
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
            if text and self._owner_answer(text, "typed"):
                return                              # "כן" / "לא" typed while an approval waits: that was the answer
            if text:
                self._run_busy(self.handle_utterance(text, typed=True))
        elif t in ("mic.set_mute", "mic.mute_changed"):
            await self.set_muted(bool(p.get("muted")))
        elif t == "status.request":
            await self.link.reply(ws, "status", self.snapshot())
        elif t == "confirm.reply":
            aid = str(p.get("id"))
            if self._confirm.get(aid) != client.id:      # only the face that was asked may answer
                logs.warn("core", "CONFIRM_SPOOFED", "confirmation reply from a client that was not asked",
                          role=client.role)
                return
            self._confirm.pop(aid, None)
            self.approvals.answer(aid, p.get("approved") is True, channel="face")
        elif t == "debug.enable":
            logs.enable_debug(min(int(p.get("minutes", self.cfg.log.debug_minutes)), self.cfg.log.debug_minutes))
            logs.info("core", "DEBUG_ENABLED", "temporary DEBUG logging enabled")
        elif t in ("core.shutdown", "desktop.close"):
            if t == "desktop.close":
                await self.link.reply(ws, "desktop.closed", {})
                if self.runtime is not None and self.runtime.window_closed():
                    return                         # M1: the window is only a display; the Core keeps running
            logs.info("core", "SHUTDOWN_REQUESTED", "shutdown requested by an authenticated client")
            self.stop_event.set()
        elif t == "runtime.set":
            await self.link.reply(ws, "runtime.state", await self.runtime.apply(p, by=client.role))
        elif t in ("camera.preview", "camera.set_enabled"):   # privacy strip buttons (camera/wire.py)
            from .camera import wire as camera_wire
            await camera_wire.on_link(self, t, p, client)
        elif t == "ui.open_dashboard":               # WP-H: the face's Dashboard button
            try:
                url = await self.open_dashboard()
                await self.link.reply(ws, "dashboard.opened", {"url": url.split("?", 1)[0],
                                                               "browser": self.open_url is not None})
            except Exception as e:
                logs.warn("core", "DASHBOARD_FAILED", f"dashboard could not start: {type(e).__name__}")
                await self.link.reply(ws, "error", {"code": "DASHBOARD_FAILED", "message": "dashboard did not start",
                                                    "action": "run `jarvis dashboard`"})
        elif t in INBOUND:
            logs.info("device", "DEVICE_EVENT", f"device event {t}", **logs.safe_fields(p, allow=("id", "muted", "state")))
            if t == "presence.changed" and getattr(self, "camera", None) is not None:
                self.camera.external_presence(p.get("present") is True)
        else:
            await self.link.reply(ws, "error", {"code": "LINK_UNKNOWN_TYPE", "message": f"unknown message type {t}"})

    async def open_dashboard(self, page: str = "") -> str:
        """Start the dashboard inside the Core (once; it shares the Core's workspace, memory and settings) and open
        it in the browser on this computer, on `page` (e.g. "#/guide/music"; kept across the token redirect).
        Returns the full one-time URL (without the page)."""
        if self._dashboard is None or not self._dashboard.running:
            from .dashboard import Dashboard
            self._dashboard = await asyncio.to_thread(
                lambda: Dashboard(self.home, self.cfg, workspace=self.workspace, memory=self.memory,
                                  settings=self.settings).start())
            ops = getattr(self, "ops", None)
            if ops is not None:      # P0.4 Q04: "Run now"/approve wake the Core's own loop (was: next 30 s poll)
                self._dashboard.ctx._scheduler = ops.scheduler
        url = self._dashboard.url
        logs.info("core", "DASHBOARD_OPENED", "dashboard opened", port=self._dashboard.port,
                  browser=self.open_url is not None)
        if self.open_url is not None:
            page = page if page.startswith("#/") and len(page) < 80 else ""
            await asyncio.to_thread(self.open_url, url + page)
        return url

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
        if self.runtime is not None:
            self.runtime.note_muted(muted)        # persists: still muted after a restart
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
        waiting = self.approvals is not None and self.approvals.has_pending()
        if self._busy and not self._busy.done() and not waiting:   # an approval's turn stays: he answers it
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
        if self.approvals is not None and self.approvals.has_pending():
            self._spawn(self._transcribe_for_approval(audio))
            return
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
        self._owner_answer(text, "voice")           # nothing waits here (talk_up routes those): a new turn
        await self.handle_utterance(text, typed=False)

    async def handle_utterance(self, text: str, typed: bool) -> None:
        if typed:
            logs.new_cid()
        await self.set_face("thinking")
        t0 = time.monotonic()
        reply = await self.brain.respond(text)
        logs.info("core", "REPLY", f"reply via {reply.source}", tool=reply.tool, ok=reply.ok, result=reply.code,
                  ms=round((time.monotonic() - t0) * 1000), text=reply.text, typed=typed,  # WP-H history: what was asked
                  utterance=f"<{len(text)} chars>" if SENSITIVE_TEXT.search(text) else text)   # codes never logged
        self.curator.observe("user", text)
        self.curator.observe("assistant", reply.text, idle=self.live is None)   # typed chat: curate when quiet
        await self.link.send_face("assistant.text", {"text": reply.text, "source": reply.source, "ok": reply.ok,
                                                     "code": reply.code})
        if self.live is None:
            await self.speak(reply.text)
        await self.set_face("happy" if reply.ok and reply.tool else "idle")
        if reply.ok and reply.tool:
            await asyncio.sleep(1.2)
            await self.set_face("idle")

    async def speak(self, text: str, cache: bool = False) -> None:
        """Speak `text`. cache=True is for FIXED phrases only (the greeting): a cached copy of the cloud voice
        plays instantly; a miss streams from the cloud and saves the result for the next start."""
        if not text or self.dev.audio is None:
            return
        ckey = SpeechCache.key(text, self.cfg.voice.voice, self.cfg.voice.speech_model) if cache else None
        if ckey and (hit := self._speech_cache.get(ckey)) is not None:
            logs.info("tts", "SPEECH_CACHE_HIT", "playing the cached greeting (instant)", audio_s=round(len(hit[0]) / hit[1], 1))
            await self.set_face("speaking")
            try:
                await self.dev.speaker_play(*hit)
            except AudioUnavailable as e:
                self.health.set("audio", "unavailable", e.message, e.action)
            return
        saved: list = []
        if hasattr(self.tts, "stream"):
            t0 = time.monotonic()
            logs.info("tts", "SPEECH_SYNTH_START", "generating streamed speech")

            async def chunks():
                first = True
                async with aclosing(self.tts.stream(text)) as source:
                    async for samples, rate in source:
                        saved.append((samples, rate))
                        if first:
                            first = False
                            logs.info("tts", "SPEECH_FIRST_AUDIO", "first speech audio received",
                                      ms=round((time.monotonic() - t0) * 1000))
                            await self.set_face("speaking")
                            logs.info("tts", "SPEECH_PLAY_START", "starting streamed speaker playback")
                        yield samples, rate
                logs.info("tts", "SPEECH_SYNTH_READY", "speech stream complete",
                          ms=round((time.monotonic() - t0) * 1000))

            try:
                async with aclosing(chunks()) as source:
                    await self.dev.speaker_play_stream(source)
                self._maybe_cache(ckey, saved)
            except TTSUnavailable as e:
                self.health.set("speech_out", "unavailable", e.message, e.action)
            except AudioUnavailable as e:
                self.health.set("audio", "unavailable", e.message, e.action)
            return
        try:
            t0 = time.monotonic()
            logs.info("tts", "SPEECH_SYNTH_START", "generating speech")
            samples, rate = await self.tts.synth(text)
            logs.info("tts", "SPEECH_SYNTH_READY", "speech generated", ms=round((time.monotonic() - t0) * 1000))
        except TTSUnavailable as e:
            self.health.set("speech_out", "unavailable", e.message, e.action)
            return
        await self.set_face("speaking")
        try:
            logs.info("tts", "SPEECH_PLAY_START", "starting speaker playback", audio_s=round(len(samples) / rate, 2))
            await self.dev.speaker_play(samples, rate)
        except AudioUnavailable as e:
            self.health.set("audio", "unavailable", e.message, e.action)

    def _rollover_allowed(self) -> bool:
        now = time.monotonic()
        self._rollovers = [t for t in self._rollovers if now - t < 600]
        if len(self._rollovers) >= 3:
            logs.warn("voice", "LIVE_RESUME_LIMIT", "conversation keeps being closed by the server; not resuming again")
            return False
        self._rollovers.append(now)
        return True

    async def _resume_live(self) -> None:
        await asyncio.sleep(0.4)
        if self.stopping or self.live is not None or self._voice_connecting:
            return
        logs.info("voice", "LIVE_RESUMED", "conversation was closed while active: reconnecting")
        await self._start_live()

    async def _chime(self, kind: str) -> None:
        """Short soft chime: "wake" = heard you, "ready" = connected, talk now."""
        if not self.cfg.voice.chime or self.dev.audio is None:
            return
        import numpy as _np
        rate = 24000
        notes = {"wake": [(880, 0.10)], "ready": [(660, 0.08), (990, 0.12)]}.get(kind, [])
        parts = []
        for hz, dur in notes:
            t = _np.arange(int(rate * dur)) / rate
            env = _np.minimum(1.0, _np.minimum(t / 0.008, (dur - t) / 0.03))      # soft attack and release
            parts.append((_np.sin(2 * _np.pi * hz * t) * env * 0.22).astype(_np.float32))
        if parts:
            try:
                await self.dev.speaker_play(_np.concatenate(parts), rate)
            except Exception:
                logs.debug("tts", "CHIME_FAILED", "could not play the chime")

    async def _start_live(self, *, greeting: str | None = None, hold_output: bool = False, ack: bool = False) -> None:
        if self.stopping:
            return
        if self.live is not None or self._voice_connecting:
            # QA 19 Oct (suite_voice): a face connecting after a failed startup started a SECOND live session while
            # the first was still open (two billed sessions, one never heard). One conversation at a time.
            logs.info("voice", "LIVE_ALREADY_RUNNING", "a live conversation is already open; not starting another")
            return
        self._warm_probes()               # P0.3: ready before session_config() builds the tool list (after connect)
        await self.link.send_face("assistant.activity", {"text": "Loading voice connection..."})
        if self._voice_budget_left_s() <= 0:
            await self.notify_error("VOICE_BUDGET", "daily live-voice limit reached",
                                    "raise ai.daily_realtime_minutes or use push-to-talk")
            return
        v = self.cfg.voice
        async def output_level(level: float) -> None:
            await self.link.send_face("speaker.level", {"level": round(level, 3)})

        audio_io = StreamingAudio(v.input_device, v.output_device, on_output_level=output_level) if self.use_real_audio else None
        max_s = min(v.session_max_s, self._voice_budget_left_s())

        def usage(seconds: float) -> None:
            self.budget.record("voice_seconds", int(round(seconds)))

        async def on_transcript(role: str, text: str) -> None:
            if role == "user":                      # the OWNER's speech (input transcription), never JARVES's
                self._owner_answer(text, "voice")
            self.curator.observe(role, text, live=True)
            await self.link.send_face("user.transcript" if role == "user" else "assistant.text",
                                      {"text": text, "source": self.voice_mode})

        async def on_end(reason: str) -> None:
            ended[0] = True
            self._hold_wake_until = 0.0
            if self.live is live_ref[0]:
                self.live = None                   # any end (user, remote close, error, budget) frees the slot
            logs.info("voice", "LIVE_ENDED", f"live conversation ended ({reason})")
            if not self.stopping:
                self._spawn(self.curator.run(f"live_{reason}"))   # learn durable facts from this conversation
            # Smooth conversation: the server closing a session (or a time limit) while the user is in the middle
            # of talking must not drop them out - reconnect at once. Idle / user / error / budget end for real.
            last = getattr(live_ref[0], "_last_activity", 0.0)
            if (reason in ("remote_closed", "timeout") and not self.stopping and self.cfg.voice.activation == "conversation"
                    and time.monotonic() - last < 60 and self._rollover_allowed()):
                self._spawn(self._resume_live())
            if self.startup["ready"] and not self.stopping:
                self._startup_stage("running", "Waiting for Hey Jarvis")
            if reason.startswith("error:") or reason == "remote_closed":
                self.health.set("voice", "degraded", f"live conversation ended: {reason}", "check internet; say Hey Jarvis to retry")
                self._spawn(self.notify_error("LIVE_ENDED", "השיחה החיה נותקה", "בדוק חיבור ואמור היי ג׳ארוויס כדי לנסות שוב"))
            elif reason == "budget":
                self._spawn(self.notify_error("AI_BUDGET", "הגעתי למגבלת השימוש היומית", "ai.daily_request_limit"))
            await self.link.broadcast("status", self.snapshot())

        def on_health(status: str, detail: str, action: str = "") -> None:
            self.health.set("voice", status, detail, action)

        live_ref: list = [None]
        ended = [False]
        async def on_emotion(emotion: str) -> None:
            from .emotion import DURATION_MS
            await self.link.send_face("face.emotion", {"emotion": emotion, "ms": DURATION_MS.get(emotion, 3000)})

        async def on_activity(kind: str, text: str = "", detail: str = "") -> None:
            await self.link.send_face("assistant.activity", {"kind": kind, "text": text, "detail": detail})

        async def on_level(level: float) -> None:
            await self.link.send_face("mic.level", {"level": round(level, 3)})

        common = dict(on_state=self.set_face, on_transcript=on_transcript, on_usage=usage)
        extra = dict(on_end=on_end, on_health=on_health, budget=self.budget)
        if greeting is not None or hold_output:
            extra.update(greeting=greeting, hold_output=hold_output)
        if self._live_factory is not None:
            live = self._live_factory(self, **common, **extra)
        elif self.voice_mode == "live":
            from .voice.live import LiveVoice
            live = LiveVoice(self.registry, self.agent.instructions, model=v.live_model,
                             voice=self.identity.voice()["live_voice"],   # "תחליף קול" override, recovered if unsupported
                             backend_model=self.cfg.ai.text_model, web_search=self.cfg.ai.web_search,
                             name=self.cfg.assistant_name, spoken_name=self.cfg.assistant_name_spoken,
                             half_duplex=v.half_duplex, max_session_s=max_s,
                             audio_io=audio_io, idle_disconnect_s=v.idle_disconnect_s, on_emotion=on_emotion,
                             on_activity=on_activity, on_level=on_level,
                             reasoning_effort=self.cfg.ai.voice_reasoning_effort,
                             service_tier=self.cfg.ai.voice_service_tier,
                             max_output_tokens=self.cfg.ai.voice_max_output_tokens, echo_tail_s=v.echo_tail_s,
                             clock_context=v.clock_context, progress_after_s=v.progress_after_s,
                             no_speech_timeout_s=(v.wake_no_speech_s if ack else 0),
                             backend_deadline_s=v.backend_deadline_s, retry_lookup=self._retry_lookup,
                             reasoning_summary=self.cfg.ai.reasoning_summary, **common, **extra)
        else:
            from .voice.realtime import RealtimeVoice
            live = RealtimeVoice(v.realtime_model, self.agent.instructions, self.registry.realtime_tools(),
                                 v.language, v.realtime_voice, v.half_duplex, audio_io, max_session_s=max_s,
                                 idle_disconnect_s=v.idle_disconnect_s, **common, **extra)
        live_ref[0] = live
        wsp.attach_live(live, self.workspace, self.memory)   # persona files + known facts in the voice instructions
        feat.attach_live(live, self.features)                 # what is off / unavailable, so he says so truthfully
        if getattr(self, "guides", None) is not None:         # WP-L: help questions -> backend; one suggestion
            self.guides.attach_live(live)
        from . import context_trace                           # LAST: records the final instructions + notes sent
        context_trace.attach_live(live, home=self.home, cfg=self.cfg, memory=self.memory, workspace=self.workspace,
                                  registry=self.registry, feats=self.features)
        logs.new_cid()
        self._voice_connecting = True
        await self.link.broadcast("status", self.snapshot())
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
        finally:
            self._voice_connecting = False
            if ack:
                await self.link.send_face("assistant.activity", {"kind": "done", "text": "", "detail": ""})
            await self.link.broadcast("status", self.snapshot())
        if getattr(live, "running", True) is False:
            return                                  # ended during start (on_end already handled it)
        if self.stopping:
            await live.stop()
            return
        self.live = live
        await self.link.broadcast("status", self.snapshot())
        for task in (self.tasks.unannounced() if self.tasks is not None else []):
            self._spawn(self._task_finished(task))     # finished while no conversation was open
        if ack:
            await self.link.send_face("assistant.activity", {"kind": "done", "text": "", "detail": ""})
            self._spawn(self._chime("ready"))
            await self.set_face("listening")
        if not hold_output and self.cfg.voice.activation == "conversation":
            self._startup_stage("running", "Voice ready")

    async def _tasks_changed(self, views: list[dict]) -> None:
        """Background task list for the screen (tasks.py docstring: message shape)."""
        await self.link.send_face("tasks.update", {"tasks": views})

    async def _task_finished(self, task) -> None:
        """A background task finished: GPT-Live says it at a good moment; without a conversation it waits for
        the next one (and stays on the screen / in tasks.jsonl)."""
        from .tasks import announcement
        live = self.live
        if task.announced or task.id in self._announcing or live is None or not hasattr(live, "announce"):
            return
        self._announcing.add(task.id)
        try:
            if await live.announce(announcement(task), tag=task.id):
                self.tasks.mark_announced(task)
        finally:
            self._announcing.discard(task.id)

    def _startup_stage(self, phase: str, detail: str) -> None:
        progress = {"starting": 5, "core_ready": 25, "loading": 45, "waiting_audio": 70,
                    "ready": 90, "running": 100}.get(phase, self.startup.get("progress", 0))
        self.startup = {"phase": phase, "ready": phase in ("ready", "running"), "detail": detail,
                        "progress": progress}
        logs.info("core", "STARTUP_STAGE", detail, phase=phase)

    async def prepare_desktop(self) -> None:
        """Essential readiness gate, reusable by a future display; it does not launch Godot itself."""
        self._startup_stage("loading", "Loading voice...")
        try:
            if self.use_real_audio and self.voice_mode != "off":
                audio = self.health.components.get("audio")
                if audio is None or audio.status != "ok":
                    raise RuntimeError("audio device is not ready; run jarvis status")
            if self.cfg.voice.activation == "conversation":
                if self.voice_mode != "live":
                    raise RuntimeError("automatic desktop startup currently requires live voice; check voice/API settings")
                hello = self.hello_text(question=True) if self.cfg.voice.greeting else None
                await self._start_live(greeting=hello, hold_output=True)
                if self.live is None or not hasattr(self.live, "wait_ready"):
                    raise RuntimeError("live voice did not become ready; check logs and API limits")
                self._startup_stage("waiting_audio", "Loading greeting audio...")
                await self.live.wait_ready()
                self._greeted = True                 # greeting comes from this same live session, never a second TTS call
            elif self.use_real_audio and self.voice_mode != "off":
                self._startup_stage("loading", "Preparing local wake microphone...")
                deadline = time.monotonic() + 20
                while not getattr(self, "_wake_mic_ready", False):
                    wake_health = self.health.components.get("wake_word")
                    if self.wake is None or (wake_health and wake_health.status in ("unavailable", "degraded")):
                        raise RuntimeError("wake listener unavailable; see jarvis status")
                    if time.monotonic() >= deadline:
                        raise RuntimeError("wake microphone did not become ready")
                    await asyncio.sleep(0.05)
            self._startup_stage("ready", "Ready. Opening the room...")
        except BaseException:
            self._startup_stage("failed", "Startup failed; the room remains closed. See logs / jarvis status.")
            if self.live is not None:               # a half-ready session must not stay open (and billed) unheard
                live, self.live = self.live, None
                try:
                    await asyncio.wait_for(live.stop(), 8)
                except Exception:
                    logs.warn("voice", "LIVE_STOP_FAILED", "half-started live voice did not stop cleanly")
            raise

    def _maybe_cache(self, ckey, saved) -> None:
        """Keep a fixed phrase only when the CLOUD voice produced it (a local fallback is fast anyway)."""
        if not ckey or not saved or not str(getattr(self.tts, "last_used", "")).startswith("openai"):
            return
        try:
            import numpy as _np
            self._speech_cache.put(ckey, _np.concatenate([s for s, _ in saved]), saved[0][1])
            logs.info("tts", "SPEECH_CACHE_SAVED", "greeting cached: next start speaks instantly")
        except Exception:
            logs.warn("tts", "SPEECH_CACHE_FAILED", "could not cache the greeting", exc=True)

    def _warm_probes(self) -> None:
        """Run the one slow feature probe (music: a localhost TCP connect, 0.3 s timeout when Pear is closed) in a
        worker thread instead of on the event loop, where TOOLS.md at start-up and live_tools() at each live-session
        start would otherwise wait for it. Fire-and-forget: a not-yet-finished warm-up just means available()
        probes itself exactly as before."""
        feats = getattr(self, "features", None)
        if feats is None or "music" not in feat.CATALOG:
            return
        try:
            asyncio.get_running_loop().run_in_executor(None, feats.warm, "music")
        except RuntimeError:
            pass

    def _prewarm_modules(self) -> list[str]:
        """What the selected route will import on first use, most urgent first (SW-V3 P0.3, measured on the owner's
        PC: openai 1.3-2.7 s, the Agents SDK another 2.2-2.8 s and +41 MB RAM). The Agents SDK is only used by the
        API-key text brain (brain.AgentBrain) and by voice.mode = "realtime"; GPT-Live and the ChatGPT-plan brain
        never import it, so a combined/ChatGPT install no longer loads it in the background while the first live
        session starts (19 Oct logs: prewarm 6-21 s, competing with LIVE_STARTED for the CPU)."""
        mods = ["openai"]
        if self.voice_mode == "live":
            # lazily imported by AsyncOpenAI().live.connect() ON THE EVENT LOOP: ~0.45 s per first live session
            mods += ["openai.resources.live.forks", "httpcore2", "jarves.voice.live"]
        elif self.voice_mode == "realtime":
            mods += ["agents.realtime", "jarves.voice.realtime"]
        if isinstance(self.agent, AgentBrain):
            mods += ["agents"]
        return mods

    def _prewarm(self) -> None:
        """Import the heavy cloud-voice libraries in a worker thread right at start-up. QA 18 Oct: importing
        agents.realtime on first use took 6 s (13 s on a busy PC) and delayed the first spoken word."""
        import importlib
        t0 = time.monotonic()
        mods = self._prewarm_modules()
        try:
            for name in mods:
                try:
                    importlib.import_module(name)
                except ImportError:
                    if name in ("openai", "agents", "agents.realtime"):
                        raise
                    # an optional submodule moved in another SDK version: first use imports what it needs
            logs.info("core", "PREWARM_DONE", "cloud voice libraries loaded", ms=round((time.monotonic() - t0) * 1000),
                      modules=mods)
        except Exception as e:
            logs.warn("core", "PREWARM_FAILED", f"{type(e).__name__}: {str(e)[:120]}")

    def _log_summary(self) -> None:
        """One INFO line that answers "what is this JARVES going to do?" (visible in the terminal at startup)."""
        v, a = self.cfg.voice, self.cfg.ai
        from .chatgpt_auth import ChatGPTAuth
        plan = ChatGPTAuth(self.home).status()
        logs.info("core", "STARTUP_SUMMARY", "configuration in effect",
                  connection=a.connection, voice_mode=self.voice_mode, activation=v.activation,
                  wake=f"{v.wake_word}@{v.wake_threshold}" if self.wake is not None else "off",
                  mic=_mic_name(v.input_device), speaker=_speaker_name(v.output_device),
                  api_key="set" if openai_key_present() else "missing",
                  chatgpt="signed-in" if plan.get("plan_usage") else "not connected",
                  greeting=v.greeting, music=self.cfg.music.provider,
                  features_off=[f.id for f, _ in self.features.unavailable_summary()],
                  say="Hey Jarvis" if self.wake is not None else "(see jarvis doctor)")

    # ------------------------------------------------------------------ "Hey Jarvis" wake word
    def _setup_wake(self) -> None:
        v = self.cfg.voice
        if not v.wake_enabled or self.voice_mode == "off" or not (self._wake_audio or self.use_real_audio):
            self.health.set("wake_word", "disabled", "wake word off (voice.wake_enabled / voice.mode)")
            return
        from .voice.wakeword import WakeListener, WakeWordUnavailable, make_wake_detector
        try:
            det = self._wake_detector or make_wake_detector(self._wake_cfg())
            ok, msg = det.check() if hasattr(det, "check") else (True, "")
            if not ok and self._wake_detector is None and v.wake_confirm:
                # the local speech check could not load (no faster-whisper / model): one stage, said truthfully
                logs.warn("wake", "WAKE_CONFIRM_UNAVAILABLE", msg, action="uv sync --extra voice; connect once "
                          "so the speech model can download", fallback=f"wake model only at {v.wake_threshold}")
                cfg1 = self._wake_cfg()
                cfg1.wake_confirm = False
                det = make_wake_detector(cfg1)
                ok, msg = det.check() if hasattr(det, "check") else (True, "")
        except WakeWordUnavailable as e:
            ok, msg = False, e.message
        if not ok:
            self.health.set("wake_word", "unavailable", msg, "check wake/audio setup: jarvis status; uv sync --extra voice")
            logs.warn("wake", "WAKE_UNAVAILABLE", msg)
            return
        self.wake = WakeListener(det, self._on_wake, v.wake_cooldown_s)
        self._woke = False
        if v.activation == "conversation" and self.voice_mode == "live":
            self._hold_wake_until = time.monotonic() + 45     # the session opens now; "Hey Jarvis" is for AFTER it ends
        self.health.set("wake_word", "ok", f"say \"{v.wake_phrase}\" (detected on this device, {Path(v.wake_word).name})")
        self._bg.append(asyncio.create_task(self._wake_loop()))

    def _wake_cfg(self):
        """voice config for the wake pipeline, with this mic's saved starting gain (voice/wake_calib.py)."""
        from .voice.wake_calib import load_calibration
        v = self.cfg.voice.model_copy()
        if v.wake_agc and not v.wake_gain_db:
            cal = load_calibration(self.home, _mic_name(v.input_device))
            if cal:
                v.wake_gain_db = float(cal.get("gain_db", 0.0))
        return v

    def _wake_note(self, kind: str, hint: str, **kw) -> None:
        """Last wake-listener finding for the dashboard/face privacy strip (`privacy.wake`) and the status."""
        self._wake_status = {"kind": kind, "hint": hint, "ts": round(time.time(), 1), **kw}

    async def _on_wake(self, score: float) -> None:
        self._woke = True
        conf = getattr(self.wake.detector, "last_confirm", None) if self.wake is not None else None
        via = "asr_confirm" if conf and conf.get("ok") and score <= getattr(self.wake.detector, "accept", 1) else "model"
        logs.info("wake", "WAKE_WORD", "wake word heard", score=round(score, 2), via=via,
                  **({"asr_ms": conf["ms"]} if via == "asr_confirm" else {}))
        self._wake_note("woke", "", score=round(score, 2), via=via)

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
        return not (not self.cfg.voice.wake_enabled or self.dev.mic_muted or self.live is not None or self.listening or self.stopping
                    or self._voice_connecting or time.monotonic() < self._hold_wake_until
                    or (self.runtime is not None and self.runtime.recovering)
                    or (self._busy is not None and not self._busy.done()))

    async def _wake_loop(self) -> None:
        """One local mic stream at 16 kHz: wake word -> live conversation, or capture-until-silence -> answer.
        The stream is closed (device released) while muted, during a live session and while JARVES answers."""
        from .voice.wakeword import capture_until_silence, level_meter
        v = self.cfg.voice
        load = getattr(self.wake.detector, "load", None)
        if load is not None:
            try:
                await load()
            except Exception:
                logs.error("wake", "WAKE_LOAD_FAILED", "wake listener failed to initialize", exc=True)
                self.health.set("wake_word", "unavailable", "wake listener failed to initialize", "see logs; use Talk")
                return
        from .voice.wake_calib import load_calibration, save_calibration
        from .voice.wake_confirm import SpeechMonitor
        last_report = time.monotonic()
        peak_score = 0.0
        peak_level = 0.0
        near_t, near_n = 0.0, 0
        monitor = SpeechMonitor(min_score=NEAR_MISS_MIN)
        heard_t, heard_n, confirm_seen = -1e9, 0, None
        mic_name = _mic_name(v.input_device)
        calibrated = load_calibration(self.home, mic_name) is not None
        while not self.stopping:
            if not self._wake_should_listen():
                await asyncio.sleep(0.2)
                continue
            mic = self._wake_audio() if self._wake_audio else StreamingAudio(v.input_device, v.output_device, rate=16000)
            from .runtime.power import stall_guard          # unplugged headset / after sleep: no frames -> re-open
            chunks = self._levels(stall_guard(mic.mic_chunks(16000, 2560), self.cfg.runtime.mic_stall_s))  # 80 ms
            woke = False
            try:
                self.wake.resume()
                first_frame = True
                async for chunk in chunks:
                    if not self._wake_should_listen():
                        break
                    self._woke = False
                    await self.wake.process(chunk)
                    peak_score = max(peak_score, self.wake.stats.last_score)
                    if self.wake.stats.frames > 8:           # skip the mic's start-up pop (~0.6 s) in the level stats
                        peak_level = max(peak_level, level_meter(chunk))
                    if first_frame:
                        first_frame = False
                        self._wake_mic_ready = True
                        self._wake_mic_active = True
                        self.health.set("wake_word", "ok", "local wake microphone active")
                        await self.link.broadcast("status", self.snapshot())
                        logs.info("wake", "WAKE_LISTENING", "microphone active; waiting for Hey Jarvis",
                                  mic=_mic_name(v.input_device), threshold=self.wake.detector.threshold)
                    sc = self.wake.stats.last_score
                    if (not self._woke and sc >= NEAR_MISS_MIN and sc < self.wake.detector.threshold
                            and time.monotonic() - near_t > 2.0):
                        near_t, near_n = time.monotonic(), near_n + 1
                        logs.info("wake", "WAKE_NEAR_MISS", "heard something like the wake word but not sure enough",
                                  score=round(sc, 2), needs=self.wake.detector.threshold,
                                  hint="say \"Hey Jarvis\" clearly, or run `jarvis wake test` to calibrate")
                        self._wake_note("near_miss", "close: say \"Hey Jarvis\" a bit clearer",
                                        score=round(sc, 2), needs=self.wake.detector.threshold)
                    conf = getattr(self.wake.detector, "last_confirm", None)
                    if conf is not None and conf is not confirm_seen:
                        confirm_seen = conf
                        if not conf["ok"]:      # the transcript stays in RAM: room speech is not written to logs
                            logs.info("wake", "WAKE_CONFIRM_REJECTED", "wake model was unsure and the local speech "
                                      "check did not hear the name", score=conf["score"], asr_ms=conf["ms"])
                    ev = None if self._woke else monitor.feed(chunk, sc)
                    if ev is not None and time.monotonic() - heard_t > 30:
                        heard_t, heard_n = time.monotonic(), heard_n + 1
                        if ev.kind == "too_quiet":
                            hint = ("mic too quiet: move closer, raise the input volume, or run `jarvis wake test`")
                            logs.warn("wake", "WAKE_MIC_QUIET", "heard speech, but too quiet for the wake word",
                                      level_db=ev.level_db, floor_db=ev.floor_db, peak_score=ev.peak_score,
                                      mic=mic_name, hint=hint)
                        else:
                            hint = "heard speech, no wake match: say 'Hey Jarvis'"
                            logs.info("wake", "WAKE_SPEECH_NO_MATCH", hint, speech_ms=ev.speech_ms,
                                      level_db=ev.level_db, peak_score=ev.peak_score, mic=mic_name,
                                      hint="say \"Hey Jarvis\" (or \"Hi Jarvis\"); `jarvis wake test` shows the score")
                        self._wake_note(ev.kind, hint, level_db=ev.level_db, peak_score=ev.peak_score)
                    if not calibrated and monitor.last_burst_db is not None and monitor.frames > 40:
                        calibrated = True     # first run with this mic: room floor + first speech level
                        cal = save_calibration(self.home, mic_name, floor_db=monitor.floor_db,
                                               speech_db=monitor.last_burst_db, verdict="auto", by="core")
                        logs.info("wake", "WAKE_CALIBRATION", "first-run level calibration for this microphone",
                                  mic=mic_name, **{k: cal[k] for k in ("floor_db", "speech_db", "gain_db")})
                        agc = getattr(self.wake.detector, "agc", None)
                        if agc is not None and cal["gain_db"] > agc.gain_db:
                            agc.gain_db = cal["gain_db"]
                    if time.monotonic() - last_report >= 15:
                        logs.info("wake", "WAKE_HEALTH", "wake listener running", frames=self.wake.stats.frames,
                                  peak_score=round(peak_score, 3), peak_level=round(peak_level, 3),
                                  threshold=self.wake.detector.threshold, near_misses=near_n,
                                  speech_no_match=heard_n, floor_db=round(monitor.floor_db, 1),
                                  **({"gain_db": round(self.wake.detector.agc.gain_db, 1)}
                                     if getattr(self.wake.detector, "agc", None) is not None else {}),
                                  mic=mic_name)
                        last_report, peak_score, peak_level = time.monotonic(), 0.0, 0.0
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
                logs.warn("wake", e.code, e.message, action=e.action)
                self.health.set("audio", "unavailable", e.message, e.action)
                self._wake_mic_active = False
                await self.link.broadcast("status", self.snapshot())
                if e.code == "AUDIO_MIC_STALLED" and self.runtime is not None:
                    await self.runtime.recover_audio("mic_stalled")     # re-scan devices (new default mic)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception:
                logs.error("wake", "WAKE_LOOP_ERROR", "wake word loop failed; retrying", exc=True)
                self.health.set("wake_word", "degraded", "wake word loop crashed; retrying", "see logs")
                await asyncio.sleep(2)
            finally:
                self._wake_mic_active = False
                self.listening = False
                self.wake.pause()
                await chunks.aclose()
                mic.close()
            if woke and self.voice_mode in ("live", "realtime"):
                # instant feedback (the connection takes ~2 s): chime + attentive face + "connecting"; no spoken
                # "yes?" so the user can talk the moment the second chime sounds
                self._spawn(self._chime("wake"))
                await self.set_face("listening")
                await self.link.send_face("assistant.activity", {"kind": "connecting", "text": "מתחבר…", "detail": ""})
                await self._start_live(ack=True)

    # ------------------------------------------------------------------ feature switches (features.py)
    def _health_components(self) -> dict:
        h = getattr(self, "health", None)
        return {k: {"status": v.status, "detail": v.detail} for k, v in h.components.items()} if h else {}

    def _write_tools_md(self) -> None:
        from .guides import index_markdown as guides_index_markdown
        self.workspace.write_generated("TOOLS.md", wsp.tools_markdown(self.registry, self.settings.view())
                                       + "\n" + guides_index_markdown())
        catalog = ["# JARVES features", "Generated from the actual feature catalog; not a promise of readiness.",
                   "Use features_list / get_status for current state; read this full file with workspace_read.",
                   "All feature IDs: " + ", ".join(feat.CATALOG) + ".",
                   self.features.capabilities_markdown(), "## What each feature does"]
        for f in feat.CATALOG.values():
            catalog.append(f"- {f.id} / {f.title_en}: {f.about or f.title_en}. "
                           f"Needs: {', '.join(f.needs) or 'none'}. Risk: {f.risk}. Applies: {f.applies}.")
        self.workspace.write_generated("FEATURES.md", "\n".join(catalog) + "\n")

    def _feature_changed(self, fid: str, on: bool) -> None:
        """A switch changed (voice tool, dashboard, CLI): apply what is cheap now; the rest says when it applies."""
        self.features.apply_to_config(self.cfg)
        self.features.invalidate()
        if getattr(self.agent, "_agent", None) is not None:
            self.agent._agent = None                # AgentBrain rebuilds its tool list on the next request
        if getattr(self, "workspace", None) is not None:
            self._write_tools_md()
        if fid == "wake_word" and on and self.wake is None and getattr(self, "dev", None) is not None and not self.stopping:
            self._setup_wake()
        elif fid == "wake_word" and not on and getattr(self, "health", None) is not None:
            self.health.set("wake_word", "disabled", "wake word switched off")   # the wake loop releases the mic
        restart = ((fid == "music" and on and getattr(self, "music", None) is None)
                   or (fid == "background_tasks" and on and getattr(self, "tasks", None) is None))
        logs.info("features", "FEATURE_APPLIED", f"{fid} {'on' if on else 'off'}"
                  + (" (takes effect at the next Core start)" if restart else ""), feature=fid, on=on)

    # ------------------------------------------------------------------ voice change ("תחליף קול")
    def _on_voice_change(self, voice: str, restart_now: bool) -> None:
        """GPT-Live fixes the voice per session: the override applies to the next session, or now via a restart."""
        logs.info("voice", "VOICE_CHANGED", f"voice set to {voice}", voice=voice, restart=restart_now)
        if restart_now and self.live is not None and self.voice_mode == "live":
            self._spawn(self._restart_live_for_voice(self.live))

    async def _restart_live_for_voice(self, live) -> None:
        await asyncio.sleep(1.0)                      # let the tool result reach the conversation first
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and self.live is live and (
                getattr(live, "_tools_running", 0) or getattr(live, "_pending", None)
                or time.monotonic() < getattr(live, "_speaking_until", 0.0) + 1.0):
            await asyncio.sleep(0.25)                 # the confirmation sentence is still being spoken
        if self.live is not live or self.stopping:
            return
        await live.stop("voice_change")
        await live.wait_ended() if hasattr(live, "wait_ended") else None
        self.live = None
        await self._start_live(greeting="זה הקול החדש שלי. מה דעתך?")

    # ------------------------------------------------------------------ confirmation for side effects
    async def ask_confirm(self, prompt: str, timeout: float | None = None, **meta) -> bool:
        """registry.confirm / camera switch / any approval: the shared approvals service (approvals.py) shows it
        on the popup, tray, face, dashboard and voice at once; no answer in time = NO."""
        if self.stopping or self.approvals is None:
            return False
        ok = await self.approvals.ask(prompt, timeout=timeout, **meta)
        if not ok:
            logs.info("core", "CONFIRM_DENIED", "approval not given: treated as NO")
        return ok
    ask_confirm.accepts_meta = True

    def _owner_answer(self, text: str, channel: str) -> bool:
        """The owner's own words (typed on the face / heard from the mic): a pure yes/no answers the ONE waiting
        approval. Otherwise it is a new turn (a denied request may be asked again)."""
        ap = self.approvals
        if ap is None:
            return False
        if ap.has_pending() and ap.answer_words(text, channel=channel):
            logs.info("core", "APPROVAL_ANSWERED_BY_WORDS", f"approval answered ({channel})")
            return True
        ap.note_user_turn()
        return False

    async def _transcribe_for_approval(self, audio) -> None:
        """Push-to-talk while an approval waits: the turn that asked must not be cancelled; hear yes/no only."""
        try:
            text = await self.stt.transcribe(audio)
        except STTUnavailable as e:
            await self.notify_error(e.code, e.message, e.action)
            return
        if text:
            await self.link.send_face("user.transcript", {"text": text})
            if not self._owner_answer(text, "voice"):
                await self.link.send_face("assistant.text", {"text": "מחכה לאישור שלך: כן או לא (או על המסך).",
                                                             "source": "core"})
