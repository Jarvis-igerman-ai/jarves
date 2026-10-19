"""Configuration: one small TOML file + environment secrets. Unknown keys and secrets in the file are rejected."""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Raised for an unreadable or invalid config; message says what to fix."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LinkConfig(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1024, le=65535)

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, v: str) -> str:
        # SW-V1 policy: no LAN/remote listener until a secured admin surface is approved.
        if v not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("link.host must be a loopback address (127.0.0.1); LAN access is not approved yet")
        return v


class AIConfig(_Strict):
    # Model IDs checked 2026-10-18 at developers.openai.com/api/docs/models/all (GPT-6 = current flagship family)
    provider: Literal["openai", "offline"] = "openai"
    # How JARVES pays for AI (user's explicit choice; never switched automatically):
    #   api_key  = everything on OPENAI_API_KEY (pay-as-you-go): GPT-6 text + GPT-Live voice
    #   chatgpt  = text on your ChatGPT plan (Sign in with ChatGPT); voice stays local; no API spending at all
    #   combined = text on your ChatGPT plan + GPT-Live voice and cloud speech billed to OPENAI_API_KEY
    connection: Literal["api_key", "chatgpt", "combined"] = "api_key"
    chatgpt_model: str = ""                 # "" = first model your plan lists (`jarves chatgpt models`)
    text_model: str = "gpt-6-luna"          # API-key backend for typed chat and GPT-Live delegation; "gpt-6.1-sol" = smarter
    web_search: bool = True
    max_turns: int = Field(6, ge=1, le=20)
    timeout_s: float = Field(30, gt=0, le=120)
    daily_request_limit: int = Field(200, ge=0)   # hard cost ceiling: model requests per local day (0 = AI off)
    daily_realtime_minutes: int = Field(30, ge=0)  # hard cost ceiling for live voice minutes per local day
    # GPT-Live backend (Responses delegation) speed, measured 2026-10-19 (docs/reviews/LIVE_CONVERSATION_TUNING.md):
    # the model default effort is "medium"; spoken lookups need the lowest effort that still answers correctly.
    voice_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", ""] = "none"  # "" = model default
    # "priority" = Fast mode (up to 2.5x faster, 2x token price; gpt-6-luna is ~$0.0005 per lookup). "" = account default
    voice_service_tier: Literal["", "auto", "default", "flex", "priority"] = "priority"
    voice_max_output_tokens: int = Field(800, ge=0, le=8000)   # per delegated answer (incl. reasoning); 0 = no cap
    # Reasoning summaries in the DEBUG log ("how he thinks"): requested wherever the backend reasons (not with
    # effort "none"). "" = off.
    reasoning_summary: Literal["", "auto", "concise", "detailed"] = "auto"
    # Background tasks (tasks.py): research runs in the Core while the conversation goes on.
    tasks_enabled: bool = True
    task_max_running: int = Field(3, ge=1, le=10)
    task_timeout_s: float = Field(180, ge=10, le=900)
    research_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", ""] = "low"


class VoiceConfig(_Strict):
    activation: Literal["wake_word", "conversation"] = "wake_word"
    # live = OpenAI GPT-Live (best, full duplex); realtime = gpt-realtime-2.1 via Agents SDK (alternative);
    # push_to_talk = offline fallback (local STT). Without OPENAI_API_KEY the Core uses push_to_talk.
    mode: Literal["live", "realtime", "push_to_talk", "off"] = "live"
    language: str = "he"
    live_model: str = "gpt-live-1"
    realtime_model: str = "gpt-realtime-2.1"
    speech_model: str = "gpt-realtime-2.1-mini"   # speaks typed replies (OpenAI's TTS successor)
    voice: str = "vesper"
    realtime_voice: str = "cedar"          # Realtime has a different supported voice list from Live
    idle_disconnect_s: int = Field(180, ge=30, le=600)
    half_duplex: bool = True               # mic gated while JARVES speaks (laptop speakers); False with headphones/AEC mic
    echo_tail_s: float = Field(0.4, ge=0, le=2)    # half-duplex: mic stays gated this long after JARVES stops (room echo)
    # GPT-Live gets the local time as silent context once a minute (session.thinking.append), so "what time is it"
    # is answered at once instead of a ~2-3 s backend round trip.
    clock_context: bool = True
    # A backend lookup still running after this long with nothing said: ask GPT-Live for ONE short neutral "still checking"
    # (session.commentary.append). 0 = off.
    progress_after_s: float = Field(2.5, ge=0, le=30)
    # Hard deadline for one delegated backend response (web lookups measured 2-13 s): after it the Core gives up on
    # it, retries ONCE itself (own 25 s deadline) and then says the result or "לא הצלחתי להביא את זה". 0 = off.
    backend_deadline_s: float = Field(25, ge=0, le=120)
    # One conversation stays open until it is quiet for idle_disconnect_s. This is only a safety ceiling per
    # session; the real cost guard is ai.daily_realtime_minutes (a session never outlives the day's budget).
    session_max_s: int = Field(3600, ge=30, le=3600)
    tts_engine: Literal["auto", "openai", "local", "none"] = "auto"
    # offline FALLBACK STT, measured 2026-10-18 on a Ryzen 3 5300U (int8, 2 s Hebrew phrase):
    # "small" ~4 s, minor errors; "ivrit-ai/whisper-large-v3-turbo-ct2" accurate but ~19 s. Pi 5: UNTESTED.
    stt_model: str = "small"
    stt_fallback_model: str = "tiny"
    stt_compute: str = "int8"
    tts_voice_hint: str = "he-IL"
    wake_no_speech_s: float = Field(12, ge=0, le=120)   # a wake-started conversation where nobody speaks ends after this (false wake); 0 = off
    chime: bool = True                     # soft sound when "Hey Jarvis" is heard and when the conversation is ready
    greeting: bool = True                  # say hello + how to talk when the face first connects
    # the hello starts this long after the face/room is ready (19 Oct: starting the same instant felt abrupt)
    greeting_delay_s: float = Field(0.5, ge=0, le=5)
    # "Hey Jarvis" wake word: detected ON THIS DEVICE (pyopen-wakeword, openWakeWord model); no audio leaves the
    # device until it triggers. Software mute stops it and releases the microphone.
    wake_enabled: bool = True
    wake_word: str = "hey_jarvis"
    # 0.5 is the model's textbook value, but real voices/accents/mics ("hi garvis") often peak at 0.2-0.4:
    # QA 18 Oct saw a real user peak at 0.17. Run `jarvis wake test` to calibrate to YOUR voice and mic.
    wake_threshold: float = Field(0.35, ge=0, le=1)
    wake_cooldown_s: float = Field(2.0, ge=0, le=30)
    record_max_s: float = Field(15, gt=0, le=60)
    input_device: int | str | None = None
    output_device: int | str | None = None


class MusicConfig(_Strict):
    provider: Literal["pear", "none"] = "pear"
    pear_url: str = "http://127.0.0.1:26538"
    timeout_s: float = Field(5, gt=0, le=30)
    max_volume: int = Field(70, ge=0, le=100)
    volume_step: int = Field(10, ge=1, le=50)      # "louder"/"quieter" move the slider this many points
    # Every 6th play removes up to 12 already-PLAYED items from Pear's queue (its visible play history) so the
    # queue stays small and fast; current and upcoming songs are never touched. false = never edit the queue.
    trim_queue: bool = True


LogLevel = Literal["ERROR", "WARNING", "INFO", "DEBUG", "VERBOSE"]


class LogConfig(_Strict):
    level: LogLevel = "INFO"                  # log file
    console_level: LogLevel = "INFO"          # terminal (`jarvis --log-level debug` overrides)
    modules: list[str] = []                   # terminal: show only these modules, e.g. ["voice", "wake"]
    hide: list[str] = []                      # terminal: hide these modules, e.g. ["link"]
    # privacy = true masks what you said/heard/answered in logs (for daily use / sharing). Development default
    # false: full content. API keys, tokens and passwords are masked in BOTH modes; `jarvis diag` always masks.
    privacy: bool = False
    max_bytes: int = Field(1_000_000, ge=10_000, le=50_000_000)
    backups: int = Field(5, ge=1, le=50)
    debug_minutes: int = Field(30, ge=1, le=240)


class ModelPrice(_Strict):
    input: float = Field(ge=0)              # USD per 1M input tokens
    cached_input: float = Field(0, ge=0)
    output: float = Field(ge=0)


class PricingConfig(_Strict):
    """Cost ESTIMATES for `jarvis cost` and the dashboard (USD). Checked 2026-10-19 at
    https://developers.openai.com/api/docs/pricing (gpt-live-1: $0.05/min billed per second; backend Responses calls
    at the model's normal token price; web search $10 per 1k calls). Your OpenAI bill is the truth."""
    voice_per_minute: dict[str, float] = {
        "gpt-live-1": 0.05,
        # Realtime is billed by audio tokens ($32/$64 per 1M for 2.1, $10/$20 for mini): rough per-minute estimate
        "gpt-realtime-2.1": 0.10, "gpt-realtime-2.1-mini": 0.03}
    models: dict[str, ModelPrice] = {
        "gpt-6-luna": ModelPrice(input=0.10, cached_input=0.01, output=0.50),
        "gpt-6-sol": ModelPrice(input=2.00, cached_input=0.20, output=10.00),
        "gpt-6.1-sol": ModelPrice(input=2.00, cached_input=0.10, output=10.00),
        "gpt-6-astra": ModelPrice(input=10.00, cached_input=1.00, output=50.00)}
    tier_multiplier: dict[str, float] = {"priority": 2.0, "flex": 0.5}   # Fast = 2x, Flex = 0.5x standard
    web_search_per_call: float = Field(0.01, ge=0)
    # A backend response whose token usage was not logged is estimated with these token counts.
    est_input_tokens: int = Field(3000, ge=0)
    est_output_tokens: int = Field(150, ge=0)
    session_alert_usd: float = Field(0.50, ge=0)   # self-diagnosis flags a conversation above this
    daily_alert_usd: float = Field(2.00, ge=0)     # ...and a day above this


class HistoryConfig(_Strict):
    """Conversation history (store.db) for the dashboard, cost and self-diagnosis. Follows log.privacy: with
    privacy on, only lengths and timings are kept, never what was said."""
    enabled: bool = True
    keep_days: int = Field(90, ge=1, le=3650)
    # self-diagnosis thresholds
    dead_air_s: float = Field(5.0, gt=0, le=120)       # silence before a reply / while a request is pending
    stall_s: float = Field(20.0, gt=0, le=600)         # a request with no real answer for this long
    slow_tool_s: float = Field(5.0, gt=0, le=120)
    slow_backend_s: float = Field(10.0, gt=0, le=600)
    early_drops: int = Field(5, ge=1)                  # early-response audio drops per session before it is an issue
    # Optional AI review (diagnose.py): after a finished voice conversation ONE short backend request
    # (llm_review_model, "" = ai.text_model) looks for problems the rules miss. API key route only, through the
    # daily Budget, at most llm_review_daily_max a day; skipped with log.privacy on (it needs the transcript).
    llm_review: bool = True
    llm_review_daily_max: int = Field(20, ge=0, le=200)
    llm_review_model: str = ""


class CameraConfig(_Strict):
    """Camera (SW-V2 WP-J, docs/CAMERA_SETUP.md). Turning the camera ON is never done here: the dashboard switches
    `camera`, `face_recognition` and `camera_when_away` (all default OFF) decide. These are only the mechanics."""
    backend: Literal["auto", "picamera2", "opencv", "none"] = "auto"   # auto: picamera2 on the Pi, else OpenCV
    device: int = Field(0, ge=0, le=9)                  # OpenCV device index (USB webcam on a PC)
    preview_width: int = Field(320, ge=160, le=1280)    # low-res stream for local presence detection only
    preview_height: int = Field(240, ge=120, le=960)
    presence_fps: float = Field(4.0, ge=0.5, le=10)
    away_after_s: float = Field(900, ge=30, le=86400)   # nobody seen and no conversation this long -> away
    greet: bool = False                                 # say a short hello when someone sits down (no listening)
    mirror_gaze: bool = True                            # the camera sees the room mirrored relative to the face
    still_max_side: int = Field(1280, ge=320, le=4096)  # a cloud photo is downscaled to this (pixels, long side)
    daily_cloud_photos: int = Field(40, ge=0, le=1000)  # photos sent to the cloud per day ("מה זה?"); 0 = never
    vision_model: str = ""                              # "" = ai.text_model (gpt-6-luna, text+image input)


class RuntimeConfig(_Strict):
    """Always-on desktop runtime (SW-V2 M1, jarves/runtime/). The switches `background_core`, `tray_icon` and
    `autostart` (jarvis features / dashboard) win over keep_running / tray once the user sets them."""
    keep_running: bool = True             # closing the window only hides it; Quit (tray) / `jarvis stop` stop the Core
    tray: bool = True                     # notification-area icon in background/desktop mode
    viewer_exe: str = ""                  # Godot executable for the window ("" = GODOT_EXE / WinGet / PATH)
    resume_gap_s: float = Field(30, ge=5, le=3600)   # wall-clock jump this large = the computer slept
    mic_stall_s: float = Field(6, ge=0, le=60)       # wake mic silent (no frames at all) this long = re-open; 0 = off


class Config(_Strict):
    assistant_name: str = "JARVES"
    # how the name is SAID (and written in Hebrew speech/greetings): the voice read the Latin "JARVES" letter by
    # letter / wrongly in the 19 Oct session. Written forms (logs, window titles) keep assistant_name.
    assistant_name_spoken: str = "ג'ארוויס"
    link: LinkConfig = LinkConfig()
    ai: AIConfig = AIConfig()
    voice: VoiceConfig = VoiceConfig()
    music: MusicConfig = MusicConfig()
    log: LogConfig = LogConfig()
    pricing: PricingConfig = PricingConfig()
    history: HistoryConfig = HistoryConfig()
    camera: CameraConfig = CameraConfig()
    runtime: RuntimeConfig = RuntimeConfig()


SECRET_KEYS = ("api_key", "apikey", "token", "secret", "password", "cookie")


def state_dir() -> Path:
    """Per-user data dir (logs, memory db, link token). Override with JARVES_HOME."""
    if os.environ.get("JARVES_HOME"):
        p = Path(os.environ["JARVES_HOME"])
    elif sys.platform == "win32":
        p = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "jarves"
    else:
        p = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "jarves"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _find_secrets(d: dict, prefix: str = "") -> list[str]:
    out = []
    for k, v in d.items():
        if isinstance(v, dict):
            out += _find_secrets(v, f"{prefix}{k}.")
        elif any(s in k.lower() for s in SECRET_KEYS):
            out.append(prefix + k)
    return out


def load_config(path: Path | None = None) -> tuple[Config, Path]:
    path = path or state_dir() / "config.toml"
    if not path.exists():
        return Config(), path
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}. Fix the TOML syntax or delete the file to use defaults.") from e
    bad = _find_secrets(raw)
    if bad:
        raise ConfigError(f"{path}: secrets are not allowed in the config file ({', '.join(bad)}). "
                          "Put OPENAI_API_KEY in the environment instead.")
    try:
        return Config.model_validate(raw), path
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        raise ConfigError(f"{path}: invalid settings: {msgs}") from e


SECRET_ENV = ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY")   # admin key: optional, read-only Costs API (usage.py)


def secrets_file(home: Path | None = None) -> Path:
    """User-private key file OUTSIDE the repo: <state_dir>/secrets.env with lines like OPENAI_API_KEY=sk-..."""
    return (home or state_dir()) / "secrets.env"


def load_secrets(home: Path | None = None) -> list[str]:
    """Load allowed keys from secrets.env into the environment; a real environment variable wins."""
    loaded = []
    try:
        lines = secrets_file(home).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return loaded
    for line in lines:
        k, sep, v = line.strip().partition("=")
        k, v = k.strip().removeprefix("export ").strip(), v.strip().strip('"').strip("'")
        if sep and k in SECRET_ENV and v and not os.environ.get(k, "").strip():
            os.environ[k] = v
            loaded.append(k)
    return loaded


def save_secret(name: str, value: str, home: Path | None = None) -> Path:
    if name not in SECRET_ENV:
        raise ValueError(f"unsupported secret {name}")
    path = secrets_file(home)
    keep = []
    if path.exists():
        keep = [l for l in path.read_text(encoding="utf-8-sig").splitlines() if l.partition("=")[0].strip() != name]
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# JARVES private keys (never commit or share this file)\n" if not keep else "")
        f.write("\n".join(keep + [f"{name}={value.strip()}"]) + "\n")
    os.replace(tmp, path)
    os.environ[name] = value.strip()
    return path


def openai_key_present() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def api_billing_allowed(ai: AIConfig) -> bool:
    """True when the user's connection choice permits billable API calls AND a key is set."""
    return ai.provider == "openai" and ai.connection in ("api_key", "combined") and openai_key_present()
